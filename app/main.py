"""main.py – CPFI Gatepass system."""
import asyncio, datetime as dt, os, secrets, socket, string
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from app import auth, pdf_slip, qr_manager
from app.database import get_db, init_db, SessionLocal
from app.events import broker
from app.models import (
    AppSetting, AuditLog, DEPARTMENTS, DEPT_ROLES,
    Gatepass, GatepassStatus,
    GuardResetRequest, Notification, PasswordResetToken, ResetRequestStatus,
    Staff, User, UserRole,
)
# Admin role removed — no admin panel or admin-level approvals
from app.schemas import GatepassCreate, OicGatepassCreate, PasswordReset, UserCreate, UserUpdate

_APPROVER_ROLES = [UserRole.DEPT_HEAD, UserRole.DEPT_OIC]
RESET_TOKEN_HOURS = 1

async def _bg_cleanup():
    while True:
        await asyncio.sleep(3600)
        try:
            db = SessionLocal()
            now = dt.datetime.now()
            cutoff = now - dt.timedelta(hours=24)

            # Archive old resolved gatepasses
            for gp in db.query(Gatepass).filter(
                Gatepass.is_archived == False,
                Gatepass.status.in_([GatepassStatus.APPROVED, GatepassStatus.DENIED,
                                     GatepassStatus.COMPLETED, GatepassStatus.CANCELLED]),
                Gatepass.decided_at < cutoff,
            ).all():
                gp.is_archived = True; gp.archived_at = now
            db.query(Notification).filter(Notification.created_at < cutoff).delete()
            db.query(AuditLog).filter(AuditLog.created_at < cutoff).delete()

            # Auto-clear on-leave for users whose return date has passed (after 6:00 AM)
            if now.hour >= 6:
                today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
                for user in db.query(User).filter(
                    User.is_on_leave == True,
                    User.leave_until != None,
                    User.leave_until <= today_6am,
                ).all():
                    user.is_on_leave = False
                    user.leave_until = None

            db.commit()
        except Exception: pass
        finally:
            try: db.close()
            except: pass

@asynccontextmanager
async def lifespan(app):
    init_db()
    db = SessionLocal()
    try:
        auth.ensure_defaults(db)
        if not db.query(AppSetting).filter(AppSetting.key == "qr_password").first():
            db.add(AppSetting(key="qr_password", value="cpfi2024"))
            db.commit()
    finally:
        db.close()
    task = asyncio.create_task(_bg_cleanup())
    yield
    task.cancel()

app = FastAPI(title="CPFI Gatepass", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# ---- Helpers ---- #
def _get_setting(db, key, default=None):
    s = db.query(AppSetting).filter(AppSetting.key == key).first()
    return s.value if s else default

def _set_setting(db, key, value):
    s = db.query(AppSetting).filter(AppSetting.key == key).first()
    if s: s.value = value
    else: db.add(AppSetting(key=key, value=value))
    db.commit()

def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
    except: return "127.0.0.1"
    finally: s.close()

def get_base_url(r: Request):
    o = os.environ.get("GATEPASS_BASE_URL")
    return o or f"http://{_lan_ip()}:{r.url.port or 8000}"

def _notify(db, msg, ntype="request", rid=None, target_role=None, target_dept=None, target_user_id=None):
    n = Notification(message=msg, type=ntype, related_id=rid,
                     target_role=target_role, target_dept=target_dept,
                     target_user_id=target_user_id)
    db.add(n); db.commit(); db.refresh(n); return n

def _audit(db, action, details, by):
    db.add(AuditLog(action=action, details=details, performed_by=by)); db.commit()

def _who(request):
    i = auth.session_info(request); return i["username"] if i else "system"

def _who_dept(request):
    i = auth.session_info(request); return (i or {}).get("department", "")

def _who_id(request):
    i = auth.session_info(request); return (i or {}).get("user_id")

def _temp_pw():
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))

# ---- Pages ---- #
@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    i = auth.session_info(request)
    return RedirectResponse(auth.role_dashboard(i["role"]) if i else "/login")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    i = auth.session_info(request)
    if i: return RedirectResponse(auth.role_dashboard(i["role"]))
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_submit(username: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    sid, info = auth.authenticate(db, username, password)
    if not sid: return RedirectResponse("/login?error=1", status_code=303)
    resp = RedirectResponse(auth.role_dashboard(info["role"]), status_code=303)
    resp.set_cookie(auth.COOKIE_NAME, sid, httponly=True, samesite="lax")
    return resp

@app.get("/logout")
def logout(request: Request):
    auth.logout(request.cookies)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp

@app.get("/admin-panel", response_class=HTMLResponse)
def admin_panel_redirect(request: Request):
    # Admin role removed — redirect to login
    return RedirectResponse("/login", status_code=303)

@app.get("/dept", response_class=HTMLResponse)
def dept_page(request: Request):
    info = auth.session_info(request)
    if not info or info["role"] not in DEPT_ROLES: return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("dept_admin.html", {
        "request": request, "role": info["role"],
        "username": info.get("username", ""),
        "dept_name": info.get("department", ""),
        "is_oic": info["role"] == "dept_oic",
        "is_on_leave": info.get("is_on_leave", False),
        "leave_until": info.get("leave_until", None),
        "user_id": info.get("user_id"),
    })

@app.get("/dept/reports", response_class=HTMLResponse)
def dept_reports(request: Request):
    info = auth.session_info(request)
    if not info or info["role"] not in DEPT_ROLES: return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("reports.html", {
        "request": request, "role": info["role"],
        "username": info.get("username", ""),
        "dept_name": info.get("department", ""),
    })

@app.get("/dept/archive", response_class=HTMLResponse)
def dept_archive(request: Request):
    info = auth.session_info(request)
    if not info or info["role"] not in DEPT_ROLES: return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("archive.html", {
        "request": request, "role": info["role"],
        "username": info.get("username", ""),
        "dept_name": info.get("department", ""),
    })

@app.get("/guard", response_class=HTMLResponse)
def guard_page(request: Request):
    info = auth.session_info(request)
    if not info or info["role"] != "guard": return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("guard.html", {
        "request": request, "role": "guard",
        "username": info.get("username", ""),
    })

@app.get("/guard/archive", response_class=HTMLResponse)
def guard_archive(request: Request):
    info = auth.session_info(request)
    if not info or info["role"] != "guard": return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("guard_archive.html", {
        "request": request,
        "username": info.get("username", ""),
    })

@app.get("/it", response_class=HTMLResponse)
def it_page(request: Request):
    info = auth.session_info(request)
    if not info or info["role"] != "it": return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("it.html", {
        "request": request, "role": "it",
        "username": info.get("username", ""),
    })

@app.get("/nurse", response_class=HTMLResponse)
def nurse_page(request: Request):
    info = auth.session_info(request)
    if not info or info["role"] != "nurse": return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("nurse.html", {
        "request": request, "role": "nurse",
        "username": info.get("username", ""),
    })

@app.get("/scan-confirm", response_class=HTMLResponse)
@app.get("/gatepass", response_class=HTMLResponse)
def staff_page(request: Request, token: str | None = None):
    return templates.TemplateResponse("gatepass.html", {"request": request, "token": token or ""})

@app.get("/oic-gatepass", response_class=HTMLResponse)
def oic_gatepass_page(request: Request):
    info = auth.session_info(request)
    if not info or info["role"] != "dept_oic":
        return RedirectResponse("/login?next=oic-gatepass", status_code=303)
    return templates.TemplateResponse("oic_gatepass.html", {
        "request": request,
        "role": info["role"],
        "username": info["username"],
        "user_id": info["user_id"],
        "dept_name": info.get("department", ""),
    })

# ---- Forgot/Reset password ---- #
@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_pw_page(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request})

@app.post("/forgot-password")
async def forgot_pw_submit(username: str = Form(...), db=Depends(get_db)):
    user = db.query(User).filter(
        ((User.username == username) | (User.email == username)), User.is_active == True
    ).first()
    if not user: return RedirectResponse("/forgot-password?error=notfound", status_code=303)
    # All roles get a time-limited reset link sent to their registered email.
    # (In this demo the link is shown on screen; in production it would be emailed.)
    tok = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(user_id=user.id, token=tok,
           expires_at=dt.datetime.now() + dt.timedelta(hours=RESET_TOKEN_HOURS)))
    db.commit()
    return RedirectResponse(f"/forgot-password?reset_token={tok}&email={user.email}", status_code=303)

@app.get("/reset-password", response_class=HTMLResponse)
def reset_pw_page(request: Request, token: str = "", db=Depends(get_db)):
    rt = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token, PasswordResetToken.used == False).first()
    valid = rt is not None and rt.expires_at > dt.datetime.now()
    return templates.TemplateResponse("reset_password.html", {"request": request, "token": token, "valid": valid})

@app.post("/reset-password")
def reset_pw_submit(token: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    rt = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token, PasswordResetToken.used == False).first()
    if not rt or rt.expires_at < dt.datetime.now():
        return RedirectResponse(f"/reset-password?token={token}&error=expired", status_code=303)
    if len(password) < 6:
        return RedirectResponse(f"/reset-password?token={token}&error=short", status_code=303)
    user = db.get(User, rt.user_id)
    if user: user.password_hash = auth.hash_password(password)
    rt.used = True; db.commit()
    return RedirectResponse("/login?reset=1", status_code=303)

# ---- API: On-leave toggle ---- #
@app.post("/api/me/on-leave")
async def api_toggle_on_leave(request: Request, db=Depends(get_db),
                        role=Depends(auth.require_role(*DEPT_ROLES))):
    """
    Head or OIC sets themselves on leave with a return date.
    Expects JSON body: { "return_date": "YYYY-MM-DD" } when going ON leave.
    Once on leave, this endpoint cannot clear it manually — the scheduler does that.
    """
    info = auth.session_info(request)
    user = db.get(User, info["user_id"])
    if not user: raise HTTPException(404)

    # If already on leave, block manual toggle-off — only the scheduler clears it
    if user.is_on_leave:
        raise HTTPException(400, "Your leave is active until your return date. It will clear automatically.")

    # Parse the return date from the request body
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    return_date_str = body.get("return_date", "")
    if not return_date_str:
        raise HTTPException(400, "Please select a return date.")

    try:
        return_date = dt.datetime.strptime(return_date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Invalid date format.")

    today = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if return_date < today:
        raise HTTPException(400, "Return date must be today or in the future.")

    # Set on leave — return date is when the 6 AM scheduler will clear it
    leave_until = return_date.replace(hour=6, minute=0, second=0, microsecond=0)
    user.is_on_leave = True
    user.leave_until = leave_until
    db.commit()

    sid = request.cookies.get(auth.COOKIE_NAME)
    auth.update_session_leave(sid, True, return_date_str)
    return {"is_on_leave": True, "leave_until": return_date_str}

# ---- API: QR ---- #
@app.get("/api/qr")
def api_qr(request: Request, role=Depends(auth.require_role("guard", "it", "nurse", *DEPT_ROLES))):
    base = get_base_url(request)
    return {"qr": qr_manager.qr_data_url(base), "token": qr_manager.current_token(),
            "scan_url": qr_manager.build_scan_url(base)}

@app.post("/api/validate-token")
def api_validate_token(payload: dict):
    return {"valid": qr_manager.is_valid(payload.get("token", ""))}

@app.post("/api/verify-qr-password")
def api_verify_qr_password(payload: dict, db=Depends(get_db)):
    entered = payload.get("password", "")
    correct = _get_setting(db, "qr_password", "cpfi2024")
    return {"valid": entered == correct}

@app.get("/api/qr-password")
def api_get_qr_password(db=Depends(get_db), role=Depends(auth.require_role("it"))):
    return {"password": _get_setting(db, "qr_password", "cpfi2024")}

@app.post("/api/qr-password")
def api_set_qr_password(payload: dict, request: Request, db=Depends(get_db),
                        role=Depends(auth.require_role("it"))):
    newpw = payload.get("password", "").strip()
    if len(newpw) < 4: raise HTTPException(400, "Password must be at least 4 characters.")
    _set_setting(db, "qr_password", newpw)
    _audit(db, "update_setting", "QR access password updated.", _who(request))
    return {"ok": True}

# ---- API: Departments & Staff ---- #
@app.get("/api/departments")
def api_departments(): return DEPARTMENTS

@app.get("/api/staff")
def api_staff(db=Depends(get_db)):
    return [r.as_dict() for r in db.query(Staff).order_by(Staff.department, Staff.name).all()]

@app.get("/api/active-employees")
def api_active_employees(db=Depends(get_db)):
    rows = db.query(Gatepass.employee_id).filter(
        Gatepass.status.in_([GatepassStatus.PENDING_NURSE, GatepassStatus.PENDING, GatepassStatus.APPROVED]),
        Gatepass.is_archived == False,
    ).distinct().all()
    return [r[0] for r in rows]

@app.post("/api/staff")
def api_create_staff(payload: dict, db=Depends(get_db), role=Depends(auth.require_role("it"))):
    eid = payload.get("employee_id", "").strip()
    name = payload.get("name", "").strip()
    dept = payload.get("department", "").strip()
    if not eid or not name or not dept: raise HTTPException(400, "All fields required.")
    if dept not in DEPARTMENTS: raise HTTPException(400, "Invalid department.")
    if db.query(Staff).filter(Staff.employee_id == eid).first(): raise HTTPException(409, "Employee ID exists.")
    s = Staff(employee_id=eid, name=name, department=dept)
    db.add(s); db.commit(); db.refresh(s)
    _audit(db, "create_staff", f"Staff '{name}' added.", "it")
    return s.as_dict()

@app.put("/api/staff/{sid}")
def api_update_staff(sid: int, payload: dict, db=Depends(get_db), role=Depends(auth.require_role("it"))):
    s = db.get(Staff, sid)
    if not s: raise HTTPException(404)
    if "employee_id" in payload and payload["employee_id"] != s.employee_id:
        if db.query(Staff).filter(Staff.employee_id == payload["employee_id"], Staff.id != sid).first():
            raise HTTPException(409)
        s.employee_id = payload["employee_id"]
    if "name" in payload: s.name = payload["name"]
    if "department" in payload:
        if payload["department"] not in DEPARTMENTS: raise HTTPException(400, "Invalid dept.")
        s.department = payload["department"]
    db.commit(); db.refresh(s)
    _audit(db, "update_staff", f"Staff '{s.name}' updated.", "it")
    return s.as_dict()

@app.delete("/api/staff/{sid}")
def api_delete_staff(sid: int, db=Depends(get_db), role=Depends(auth.require_role("it"))):
    s = db.get(Staff, sid)
    if not s: raise HTTPException(404)
    _audit(db, "delete_staff", f"Staff '{s.name}' deleted.", "it")
    db.delete(s); db.commit(); return {"ok": True}

# ---- API: Approver lookups ---- #
# All three endpoints exclude users who are on leave (is_on_leave=True).

@app.get("/api/approvers-for-dept")
def api_approvers_for_dept(dept: str, db=Depends(get_db)):
    """Active, available (not on leave) dept heads and OICs for a given department."""
    users = db.query(User).filter(
        User.role.in_(_APPROVER_ROLES),
        User.is_active == True,
        User.is_on_leave == False,
        User.department == dept,
    ).order_by(User.role, User.username).all()
    return [{"id": u.id, "username": u.username, "role": u.role.value, "department": u.department}
            for u in users]

@app.get("/api/oic-approvers")
def api_oic_approvers(dept: str, exclude_user_id: int | None = None, db=Depends(get_db)):
    """
    For an OIC submitting their own gatepass — own department path.
    Returns active, available OICs only (no heads), excluding self.
    """
    q = db.query(User).filter(
        User.role == UserRole.DEPT_OIC,
        User.is_active == True,
        User.is_on_leave == False,
        User.department == dept,
    )
    if exclude_user_id:
        q = q.filter(User.id != exclude_user_id)
    users = q.order_by(User.username).all()
    return [{"id": u.id, "username": u.username, "role": u.role.value, "department": u.department}
            for u in users]

@app.get("/api/approvers-all-depts")
def api_approvers_all_depts(exclude_dept: str | None = None, db=Depends(get_db)):
    """
    Cross-dept fallback: all active, available heads and OICs, optionally excluding one dept.
    """
    q = db.query(User).filter(
        User.role.in_(_APPROVER_ROLES),
        User.is_active == True,
        User.is_on_leave == False,
    )
    if exclude_dept:
        q = q.filter(User.department != exclude_dept)
    users = q.order_by(User.department, User.role, User.username).all()
    return [{"id": u.id, "username": u.username, "role": u.role.value, "department": u.department}
            for u in users]

@app.get("/api/dept-head-available")
def api_dept_head_available(dept: str, db=Depends(get_db)):
    """Returns whether an active, non-on-leave dept head exists for the given department."""
    head = db.query(User).filter(
        User.role == UserRole.DEPT_HEAD,
        User.department == dept,
        User.is_active == True,
        User.is_on_leave == False,
    ).first()
    return {"available": head is not None, "username": head.username if head else None}

# ---- API: Gatepasses (staff submission) ---- #
@app.post("/api/gatepass")
async def api_create_gatepass(payload: GatepassCreate, db=Depends(get_db)):
    if not qr_manager.is_valid(payload.token): raise HTTPException(403, "Invalid QR.")
    correct_pw = _get_setting(db, "qr_password", "cpfi2024")
    if payload.qr_password != correct_pw:
        raise HTTPException(403, "Incorrect access password.")
    staff = db.query(Staff).filter(Staff.employee_id == payload.employee_id).first()
    if not staff: raise HTTPException(404, "Employee not found.")

    if payload.assigned_to_user_id:
        # Explicit delegate path: staff chose a specific approver
        approver = db.get(User, payload.assigned_to_user_id)
        if not approver or approver.role not in DEPT_ROLES or not approver.is_active or approver.is_on_leave:
            raise HTTPException(400, "Selected approver is not available.")
        # Own department: only OICs are allowed as delegates
        if approver.department == staff.department and approver.role == UserRole.DEPT_HEAD:
            raise HTTPException(400, "Delegation within your own department must be to an OIC, not the head.")
    else:
        # Default path: auto-assign to the dept head of the staff member's department
        approver = db.query(User).filter(
            User.role == UserRole.DEPT_HEAD,
            User.department == staff.department,
            User.is_active == True,
            User.is_on_leave == False,
        ).first()
        if not approver:
            raise HTTPException(400, "No available department head. Please use the delegation option to select an OIC.")

    is_sick = payload.purpose == "Sickness / Illness"
    rtime = "13:00" if payload.purpose == "Lunch Out" else (
        None if payload.purpose in ("Undertime", "Sickness / Illness") else payload.return_time)
    initial = GatepassStatus.PENDING_NURSE if is_sick else GatepassStatus.PENDING

    gp = Gatepass(
        employee_id=staff.employee_id, name=staff.name, department=staff.department,
        purpose=payload.purpose, return_time=rtime, notes=payload.notes,
        diagnosis=payload.diagnosis, status=initial,
        submitter_role="staff",
        assigned_to_user_id=approver.id,
        assigned_to_username=approver.username,
        delegated_to=approver.department,
    )
    db.add(gp); db.commit(); db.refresh(gp)

    if is_sick:
        _notify(db, f"Sickness request by {gp.name} ({gp.department}).", "request", gp.id, "nurse")
    else:
        _notify(db, f"New request from {gp.name} — {gp.purpose}.",
                "request", gp.id, target_user_id=approver.id)

    await broker.publish("new_request", gp.as_dict())
    return gp.as_dict()

    is_sick = payload.purpose == "Sickness / Illness"
    rtime = "13:00" if payload.purpose == "Lunch Out" else (
        None if payload.purpose in ("Undertime", "Sickness / Illness") else payload.return_time)
    initial = GatepassStatus.PENDING_NURSE if is_sick else GatepassStatus.PENDING

    gp = Gatepass(
        employee_id=staff.employee_id, name=staff.name, department=staff.department,
        purpose=payload.purpose, return_time=rtime, notes=payload.notes,
        diagnosis=payload.diagnosis, status=initial,
        submitter_role="staff",
        assigned_to_user_id=approver.id,
        assigned_to_username=approver.username,
        delegated_to=approver.department,
    )
    db.add(gp); db.commit(); db.refresh(gp)

    if is_sick:
        _notify(db, f"Sickness request by {gp.name} ({gp.department}).", "request", gp.id, "nurse")
    else:
        _notify(db, f"New request from {gp.name} — {gp.purpose}.",
                "request", gp.id, target_user_id=approver.id)

    await broker.publish("new_request", gp.as_dict())
    return gp.as_dict()

# ---- API: OIC submits their own gatepass ---- #
@app.post("/api/oic-gatepass")
async def api_create_oic_gatepass(payload: OicGatepassCreate, request: Request, db=Depends(get_db),
                                   role=Depends(auth.require_role("dept_oic"))):
    info = auth.session_info(request)
    user = db.get(User, info["user_id"])
    if not user: raise HTTPException(404, "User not found.")

    if payload.assigned_to_user_id:
        # Explicit delegate path: OIC chose a specific approver
        approver = db.get(User, payload.assigned_to_user_id)
        if not approver or approver.role not in DEPT_ROLES or not approver.is_active or approver.is_on_leave:
            raise HTTPException(400, "Selected approver is not available.")
        if approver.id == user.id:
            raise HTTPException(400, "You cannot assign a request to yourself.")
        # Same dept: only other OICs allowed, not the head
        if approver.department == user.department and approver.role == UserRole.DEPT_HEAD:
            raise HTTPException(400, "Delegation within your own department must be to another OIC, not the head.")
    else:
        # Default path: auto-assign to the dept head of the OIC's department
        approver = db.query(User).filter(
            User.role == UserRole.DEPT_HEAD,
            User.department == user.department,
            User.is_active == True,
            User.is_on_leave == False,
        ).first()
        if not approver:
            raise HTTPException(400, "No available department head. Please use the delegation option to select an approver.")

    is_sick = payload.purpose == "Sickness / Illness"
    rtime = "13:00" if payload.purpose == "Lunch Out" else (
        None if payload.purpose in ("Undertime", "Sickness / Illness") else payload.return_time)
    initial = GatepassStatus.PENDING_NURSE if is_sick else GatepassStatus.PENDING

    gp = Gatepass(
        employee_id=f"USER-{user.id}",
        name=user.username,
        department=user.department or "",
        purpose=payload.purpose,
        return_time=rtime,
        notes=payload.notes,
        diagnosis=payload.diagnosis,
        status=initial,
        submitter_role="dept_oic",
        assigned_to_user_id=approver.id,
        assigned_to_username=approver.username,
        delegated_to=approver.department,
    )
    db.add(gp); db.commit(); db.refresh(gp)

    if is_sick:
        _notify(db, f"Sickness request by {gp.name} ({gp.department}) [OIC].", "request", gp.id, "nurse")
    else:
        _notify(db, f"OIC request from {gp.name} ({gp.department}) — {gp.purpose}.",
                "request", gp.id, target_user_id=approver.id)

    await broker.publish("new_request", gp.as_dict())
    return gp.as_dict()

@app.get("/api/gatepass/{gid}")
def api_get_gatepass(gid: int, db=Depends(get_db)):
    gp = db.get(Gatepass, gid)
    if not gp: raise HTTPException(404)
    return gp.as_dict()

@app.get("/api/gatepass/{gid}/pdf")
def api_gatepass_pdf(gid: int, db=Depends(get_db)):
    gp = db.get(Gatepass, gid)
    if not gp: raise HTTPException(404)
    return Response(content=pdf_slip.build_gatepass_pdf(gp.as_dict()), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="gatepass-{gid}.pdf"'})

# ---- API: Cancel ---- #
@app.post("/api/gatepass/{gid}/cancel")
async def api_cancel_gatepass(gid: int, request: Request, db=Depends(get_db)):
    gp = db.get(Gatepass, gid)
    if not gp: raise HTTPException(404)
    if gp.status not in (GatepassStatus.PENDING, GatepassStatus.PENDING_NURSE):
        raise HTTPException(409, "Only pending requests can be cancelled.")
    info = auth.session_info(request)
    if info and info["role"] == "dept_oic":
        if gp.employee_id != f"USER-{info['user_id']}":
            raise HTTPException(403, "You can only cancel your own requests.")
    gp.status = GatepassStatus.CANCELLED
    gp.cancelled_at = dt.datetime.now()
    db.commit(); db.refresh(gp)
    _notify(db, f"Gatepass #{gp.id} by {gp.name} was cancelled.", "status", gp.id,
            target_user_id=gp.assigned_to_user_id)
    await broker.publish("status_update", gp.as_dict())
    return gp.as_dict()

# ---- API: List gatepasses (visibility enforced) ---- #
@app.get("/api/gatepasses")
def api_list_gatepasses(scope: str = "all", dept: str | None = None,
                        request: Request = None, db=Depends(get_db)):
    info = auth.session_info(request) if request else None
    role = info["role"] if info else None
    user_dept = info.get("department", "") if info else ""
    user_id = info.get("user_id") if info else None

    q = db.query(Gatepass)

    if scope == "history":
        q = q.filter(Gatepass.status.in_([GatepassStatus.COMPLETED, GatepassStatus.DENIED,
                                           GatepassStatus.CANCELLED]))
    else:
        q = q.filter(Gatepass.is_archived == False)

    if scope == "pending":
        q = q.filter(Gatepass.status == GatepassStatus.PENDING)
    elif scope == "pending_nurse":
        q = q.filter(Gatepass.status == GatepassStatus.PENDING_NURSE)
    elif scope == "active":
        q = q.filter(Gatepass.status.in_([GatepassStatus.PENDING, GatepassStatus.APPROVED,
                                           GatepassStatus.PENDING_NURSE]))
    elif scope == "out":
        q = q.filter(Gatepass.status == GatepassStatus.APPROVED, Gatepass.returned_at.is_(None))

    if role in DEPT_ROLES and user_id:
        # Visibility rules:
        # - PENDING/APPROVED/PENDING_NURSE: only the assigned approver sees it (exclusive)
        # - COMPLETED: visible to everyone in the same department (for the shared log)
        # - DENIED/CANCELLED: only the assigned approver sees it
        # Legacy records (no assigned_to_user_id) fall back to dept match.
        q = q.filter(
            (Gatepass.assigned_to_user_id == user_id) |
            (
                (Gatepass.status == GatepassStatus.COMPLETED) &
                (Gatepass.department == user_dept)
            ) |
            (
                (Gatepass.assigned_to_user_id.is_(None)) &
                ((Gatepass.department == user_dept) | (Gatepass.delegated_to == user_dept))
            )
        )
    elif dept:
        q = q.filter((Gatepass.department == dept) | (Gatepass.delegated_to == dept))

    return [r.as_dict() for r in q.order_by(Gatepass.created_at.desc()).all()]

@app.get("/api/gatepasses/archive")
def api_archive(request: Request, month: str | None = None, db=Depends(get_db)):
    info = auth.session_info(request)
    if not info or info["role"] not in DEPT_ROLES: raise HTTPException(401)
    user_id = info.get("user_id")
    user_dept = info.get("department", "")
    q = db.query(Gatepass).filter(Gatepass.is_archived == True).filter(
        (Gatepass.assigned_to_user_id == user_id) |
        (
            (Gatepass.status == GatepassStatus.COMPLETED) &
            (Gatepass.department == user_dept)
        ) |
        (
            (Gatepass.assigned_to_user_id.is_(None)) &
            ((Gatepass.department == user_dept) | (Gatepass.delegated_to == user_dept))
        )
    )
    if month: q = q.filter(func.strftime("%Y-%m", Gatepass.created_at) == month)
    return [r.as_dict() for r in q.order_by(Gatepass.created_at.desc()).all()]

@app.get("/api/gatepasses/archive-all")
def api_archive_all(request: Request, db=Depends(get_db)):
    info = auth.session_info(request)
    if not info or info["role"] not in ("guard", *DEPT_ROLES): raise HTTPException(401)
    rows = db.query(Gatepass).filter(Gatepass.is_archived == True).order_by(
        Gatepass.department, Gatepass.created_at.desc()).all()
    return [r.as_dict() for r in rows]

@app.get("/api/gatepasses/archive-all/pdf")
def api_archive_all_pdf(request: Request, db=Depends(get_db)):
    """PDF of all archived records — for the guard archive page."""
    info = auth.session_info(request)
    if not info or info["role"] != "guard": raise HTTPException(401)
    rows = db.query(Gatepass).filter(Gatepass.is_archived == True).order_by(
        Gatepass.department, Gatepass.created_at.desc()).all()
    data = [r.as_dict() for r in rows]
    pdf = pdf_slip.build_archive_pdf(data)
    today = dt.date.today().isoformat()
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="gatepass-archive-{today}.pdf"'})

@app.get("/api/gatepasses/report")
def api_report(request: Request, month: str | None = None, db=Depends(get_db)):
    info = auth.session_info(request)
    if not info or info["role"] not in DEPT_ROLES: raise HTTPException(401)
    dept = info.get("department", "")
    q = db.query(Gatepass).filter(Gatepass.department == dept)
    if month: q = q.filter(func.strftime("%Y-%m", Gatepass.created_at) == month)
    rows = q.order_by(Gatepass.created_at).all()
    result: dict = {}
    for gp in rows:
        key = f"{gp.name} ({gp.employee_id})"
        m = (gp.created_at or dt.datetime.now()).strftime("%Y-%m")
        if key not in result: result[key] = {"name": gp.name, "employee_id": gp.employee_id, "months": {}}
        if m not in result[key]["months"]: result[key]["months"][m] = {"total": 0, "purposes": {}}
        result[key]["months"][m]["total"] += 1
        result[key]["months"][m]["purposes"][gp.purpose] = result[key]["months"][m]["purposes"].get(gp.purpose, 0) + 1
    return list(result.values())

@app.get("/api/gatepasses/report/pdf")
def api_report_pdf(request: Request, month: str | None = None, db=Depends(get_db)):
    """PDF of the monthly report summary — for the dept head/OIC reports page."""
    info = auth.session_info(request)
    if not info or info["role"] not in DEPT_ROLES: raise HTTPException(401)
    dept = info.get("department", "")
    q = db.query(Gatepass).filter(Gatepass.department == dept)
    if month: q = q.filter(func.strftime("%Y-%m", Gatepass.created_at) == month)
    rows = q.order_by(Gatepass.created_at).all()
    result: dict = {}
    for gp in rows:
        key = f"{gp.name} ({gp.employee_id})"
        m = (gp.created_at or dt.datetime.now()).strftime("%Y-%m")
        if key not in result: result[key] = {"name": gp.name, "employee_id": gp.employee_id, "months": {}}
        if m not in result[key]["months"]: result[key]["months"][m] = {"total": 0, "purposes": {}}
        result[key]["months"][m]["total"] += 1
        result[key]["months"][m]["purposes"][gp.purpose] = result[key]["months"][m]["purposes"].get(gp.purpose, 0) + 1
    data = list(result.values())
    month_label = month or dt.date.today().strftime("%Y-%m")
    pdf = pdf_slip.build_report_pdf(dept, month_label, data)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="report-{dept}-{month_label}.pdf"'})

# ---- Nurse approve/deny ---- #
@app.post("/api/gatepass/{gid}/nurse-approve")
async def api_nurse_approve(gid: int, request: Request, payload: dict = {}, db=Depends(get_db),
                            role=Depends(auth.require_role("nurse"))):
    gp = db.get(Gatepass, gid)
    if not gp or gp.status != GatepassStatus.PENDING_NURSE: raise HTTPException(400)
    gp.status = GatepassStatus.PENDING
    gp.nurse_decided_at = dt.datetime.now()
    gp.nurse_decided_by = _who(request)
    gp.nurse_status = "approved"
    if isinstance(payload, dict) and payload.get("recommendation"):
        gp.recommendation = payload["recommendation"]
    db.commit(); db.refresh(gp)
    _notify(db, f"Nurse cleared #{gp.id} ({gp.name}). Awaiting approval.",
            "status", gp.id, target_user_id=gp.assigned_to_user_id)
    await broker.publish("status_update", gp.as_dict())
    return gp.as_dict()

@app.post("/api/gatepass/{gid}/nurse-deny")
async def api_nurse_deny(gid: int, request: Request, db=Depends(get_db),
                         role=Depends(auth.require_role("nurse"))):
    gp = db.get(Gatepass, gid)
    if not gp or gp.status != GatepassStatus.PENDING_NURSE: raise HTTPException(400)
    gp.status = GatepassStatus.DENIED
    gp.nurse_decided_at = dt.datetime.now()
    gp.nurse_decided_by = _who(request)
    gp.nurse_status = "denied"
    db.commit(); db.refresh(gp)
    await broker.publish("status_update", gp.as_dict())
    return gp.as_dict()

# ---- Guard marks departure / return ---- #
@app.post("/api/gatepass/{gid}/out")
async def api_mark_out(gid: int, request: Request, db=Depends(get_db),
                       role=Depends(auth.require_role("guard", "it", *DEPT_ROLES))):
    gp = db.get(Gatepass, gid)
    if not gp or gp.status != GatepassStatus.APPROVED: raise HTTPException(409, "Request not approved.")
    if gp.left_at: raise HTTPException(409, "Already marked as out.")
    gp.left_at = dt.datetime.now()
    if gp.purpose in ("Undertime", "Sickness / Illness"):
        gp.status = GatepassStatus.COMPLETED
    db.commit(); db.refresh(gp)
    _notify(db, f"{gp.name} has left the premises (#{gp.id}).", "status", gp.id)
    await broker.publish("status_update", gp.as_dict())
    return gp.as_dict()

@app.post("/api/gatepass/{gid}/return")
async def api_mark_return(gid: int, request: Request, db=Depends(get_db),
                          role=Depends(auth.require_role("guard", "it", *DEPT_ROLES))):
    gp = db.get(Gatepass, gid)
    if not gp or gp.status != GatepassStatus.APPROVED: raise HTTPException(409)
    gp.returned_at = dt.datetime.now()
    gp.status = GatepassStatus.COMPLETED
    db.commit(); db.refresh(gp)
    _notify(db, f"{gp.name} returned (#{gp.id}).", "status", gp.id)
    await broker.publish("status_update", gp.as_dict())
    return gp.as_dict()

# ---- Dept approve/deny ---- #
@app.post("/api/gatepass/{gid}/{action}")
async def api_decide(gid: int, action: str, request: Request, db=Depends(get_db),
                     role=Depends(auth.require_dept_role())):
    if action not in ("approve", "deny"): raise HTTPException(400)
    gp = db.get(Gatepass, gid)
    if not gp: raise HTTPException(404)
    if gp.status == GatepassStatus.CANCELLED:
        raise HTTPException(409, "This request has been cancelled.")
    uid = _who_id(request)
    who = _who(request)
    if gp.assigned_to_user_id and gp.assigned_to_user_id != uid:
        raise HTTPException(403, "This request is assigned to another approver.")
    gp.status = GatepassStatus.APPROVED if action == "approve" else GatepassStatus.DENIED
    gp.decided_at = dt.datetime.now()
    gp.decided_by = who
    gp.decided_by_name = who
    if action == "approve":
        gp.accepted_by_dept = _who_dept(request)
    db.commit(); db.refresh(gp)
    label = "approved" if action == "approve" else "denied"
    _notify(db, f"Gate Pass #{gp.id} {label}.", "status", gp.id)
    await broker.publish("status_update", gp.as_dict())
    return gp.as_dict()

# ---- Users (IT) ---- #
@app.get("/api/users")
def api_list_users(db=Depends(get_db), role=Depends(auth.require_role("it"))):
    return [u.as_dict() for u in db.query(User).order_by(User.created_at.desc()).all()]

@app.post("/api/users")
async def api_create_user(payload: UserCreate, request: Request, db=Depends(get_db),
                          role=Depends(auth.require_role("it"))):
    if db.query(User).filter(User.username == payload.username).first(): raise HTTPException(409, "Username taken.")
    if db.query(User).filter(User.email == payload.email).first(): raise HTTPException(409, "Email taken.")
    u = User(username=payload.username, email=payload.email,
             password_hash=auth.hash_password(payload.password),
             role=UserRole(payload.role), department=payload.department,
             is_active=True, is_on_leave=False)
    db.add(u); db.commit(); db.refresh(u)
    _audit(db, "create_account", f"{payload.role} '{payload.username}' created.", _who(request))
    return u.as_dict()

@app.put("/api/users/{uid}")
async def api_update_user(uid: int, payload: UserUpdate, request: Request, db=Depends(get_db),
                          role=Depends(auth.require_role("it"))):
    u = db.get(User, uid)
    if not u: raise HTTPException(404)
    if payload.username and payload.username != u.username:
        if db.query(User).filter(User.username == payload.username, User.id != uid).first():
            raise HTTPException(409)
        u.username = payload.username
    if payload.email and payload.email != u.email:
        if db.query(User).filter(User.email == payload.email, User.id != uid).first():
            raise HTTPException(409)
        u.email = payload.email
    if payload.role: u.role = UserRole(payload.role)
    if payload.department is not None: u.department = payload.department
    if payload.is_active is not None: u.is_active = payload.is_active
    db.commit(); db.refresh(u)
    _audit(db, "update_account", f"Account '{u.username}' updated.", _who(request))
    return u.as_dict()

@app.post("/api/users/{uid}/reset-password")
def api_reset_pw(uid: int, payload: PasswordReset, request: Request, db=Depends(get_db),
                 role=Depends(auth.require_role("it"))):
    u = db.get(User, uid)
    if not u: raise HTTPException(404)
    u.password_hash = auth.hash_password(payload.password); db.commit()
    _audit(db, "reset_password", f"Password reset for '{u.username}'.", _who(request))
    return {"ok": True}

@app.delete("/api/users/{uid}")
async def api_delete_user(uid: int, request: Request, db=Depends(get_db),
                          role=Depends(auth.require_role("it"))):
    u = db.get(User, uid)
    if not u: raise HTTPException(404)
    name = u.username; db.delete(u); db.commit()
    _audit(db, "delete_account", f"Account '{name}' deleted.", _who(request))
    return {"ok": True}

# ---- Guard reset requests ---- #
@app.get("/api/guard-reset-requests")
def api_guard_rr(db=Depends(get_db), role=Depends(auth.require_role("it"))):
    return [r.as_dict() for r in db.query(GuardResetRequest).order_by(GuardResetRequest.created_at.desc()).all()]

@app.post("/api/guard-reset-requests/{rid}/approve")
async def api_approve_rr(rid: int, request: Request, db=Depends(get_db), role=Depends(auth.require_role("it"))):
    rr = db.get(GuardResetRequest, rid)
    if not rr or rr.status != ResetRequestStatus.PENDING: raise HTTPException(404)
    u = db.get(User, rr.user_id)
    if not u: raise HTTPException(404)
    tp = _temp_pw(); u.password_hash = auth.hash_password(tp)
    rr.status = ResetRequestStatus.APPROVED; rr.reviewed_by = _who(request)
    rr.reviewed_at = dt.datetime.now(); rr.temp_password = tp
    db.commit(); db.refresh(rr)
    _audit(db, "reset_password", f"Reset approved for '{rr.username}'.", _who(request))
    return rr.as_dict()

@app.post("/api/guard-reset-requests/{rid}/reject")
async def api_reject_rr(rid: int, request: Request, db=Depends(get_db), role=Depends(auth.require_role("it"))):
    rr = db.get(GuardResetRequest, rid)
    if not rr or rr.status != ResetRequestStatus.PENDING: raise HTTPException(404)
    rr.status = ResetRequestStatus.REJECTED; rr.reviewed_by = _who(request); rr.reviewed_at = dt.datetime.now()
    db.commit(); return {"ok": True}

# ---- Notifications ---- #
@app.get("/api/notifications")
def api_notifs(request: Request, db=Depends(get_db),
               role=Depends(auth.require_role("it", "nurse", "guard", *DEPT_ROLES))):
    info = auth.session_info(request)
    user_role = info["role"]
    user_dept = info.get("department", "")
    user_id = info.get("user_id")
    q = db.query(Notification)
    if user_role in DEPT_ROLES:
        q = q.filter(
            (Notification.target_user_id == user_id) |
            (
                (Notification.target_user_id.is_(None)) &
                ((Notification.target_role == user_role) | (Notification.target_role.is_(None))) &
                ((Notification.target_dept == user_dept) | (Notification.target_dept.is_(None)))
            )
        )
    elif user_role == "nurse":
        q = q.filter((Notification.target_role == "nurse") | (Notification.target_role.is_(None)))
    elif user_role == "guard":
        q = q.filter((Notification.target_role == "guard") | (Notification.target_role.is_(None)))
    else:
        q = q.filter((Notification.target_role == "it") | (Notification.target_role.is_(None)))
    return [n.as_dict() for n in q.order_by(Notification.created_at.desc()).limit(100).all()]

@app.post("/api/notifications/{nid}/read")
def api_mark_read(nid: int, db=Depends(get_db),
                  role=Depends(auth.require_role("it", "nurse", "guard", *DEPT_ROLES))):
    n = db.get(Notification, nid)
    if n: n.is_read = True; db.commit()
    return {"ok": True}

@app.post("/api/notifications/read-all")
def api_mark_all_read(db=Depends(get_db),
                      role=Depends(auth.require_role("it", "nurse", "guard", *DEPT_ROLES))):
    db.query(Notification).filter(Notification.is_read == False).update({"is_read": True})
    db.commit(); return {"ok": True}

@app.get("/api/audit-log")
def api_audit(db=Depends(get_db), role=Depends(auth.require_role("it", *DEPT_ROLES))):
    return [a.as_dict() for a in db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()]

# ---- SSE ---- #
@app.get("/api/events")
async def api_events(request: Request):
    queue = broker.subscribe()
    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected(): break
                try: yield await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError: yield ": keep-alive\n\n"
        finally: broker.unsubscribe(queue)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
