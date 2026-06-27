"""auth.py – session auth, role routing, account seeding."""
import hashlib, secrets
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from app.models import User, UserRole, DEPT_ROLES

COOKIE_NAME = "gp_session"
_sessions: dict[str, dict] = {}

def hash_password(pw: str) -> str: return hashlib.sha256(pw.encode()).hexdigest()
def verify_password(pw: str, h: str) -> bool: return hash_password(pw) == h

def role_dashboard(role: str) -> str:
    if role in DEPT_ROLES: return "/dept"
    return {"guard": "/guard", "it": "/it", "nurse": "/nurse"}.get(role, "/login")

def require_admin_or_dept():
    pass  # kept for import compat, not used

def authenticate(db: Session, username: str, password: str):
    user = db.query(User).filter(
        ((User.username == username) | (User.email == username)),
        User.is_active == True
    ).first()
    if not user or not verify_password(password, user.password_hash): return None, None
    sid = secrets.token_urlsafe(24)
    info = {
        "role": user.role.value,
        "username": user.username,
        "user_id": user.id,
        "department": user.department or "",
        "is_on_leave": user.is_on_leave,
        "leave_until": user.leave_until.strftime("%Y-%m-%d") if user.leave_until else None,
    }
    _sessions[sid] = info
    return sid, info

def update_session_leave(sid: str, is_on_leave: bool, leave_until_str: str | None = None):
    """Update the on-leave flag and return date in an active session."""
    if sid and sid in _sessions:
        _sessions[sid]["is_on_leave"] = is_on_leave
        _sessions[sid]["leave_until"] = leave_until_str

def logout(cookies: dict):
    sid = cookies.get(COOKIE_NAME)
    if sid: _sessions.pop(sid, None)

def session_info(request: Request):
    sid = request.cookies.get(COOKIE_NAME)
    return _sessions.get(sid) if sid else None

def require_role(*allowed):
    def _dep(request: Request) -> str:
        info = session_info(request)
        if not info or info["role"] not in allowed:
            raise HTTPException(status_code=401, detail="Not authenticated.")
        return info["role"]
    return _dep

def require_dept_role():
    def _dep(request: Request) -> str:
        info = session_info(request)
        if not info or info["role"] not in DEPT_ROLES:
            raise HTTPException(status_code=401, detail="Not authenticated.")
        return info["role"]
    return _dep

# Dept name normalisation: CSV uses short names, DB uses full names
_DEPT_MAP = {
    "Finance":           "Finance",
    "HR":                "Human Resources",
    "Logistics":         "Logistics",
    "EMD":               "EMD",
    "Production":        "Production",
    "QA":                "Quality Assurance",
    "Fishmeal & Project":"Fishmeal & Project",
    "IE":                "Industrial Engineering",
}

def ensure_defaults(db: Session):
    """Seed all required accounts on first boot."""

    # ---- Dept heads (one per department) ---- #
    heads = [
        ("head_finance",   "head_finance@cpfi.local",  "finance123",   "Finance"),
        ("head_ie",        "head_ie@cpfi.local",       "ie123",        "Industrial Engineering"),
        ("head_hr",        "head_hr@cpfi.local",       "hr123",        "Human Resources"),
        ("head_logistics", "head_log@cpfi.local",      "logistics123", "Logistics"),
        ("head_prod",      "head_prod@cpfi.local",     "prod123",      "Production"),
        ("head_qa",        "head_qa@cpfi.local",       "qa123",        "Quality Assurance"),
        ("head_fish",      "head_fish@cpfi.local",     "fish123",      "Fishmeal & Project"),
        ("head_emd",       "head_emd@cpfi.local",      "emd123",       "EMD"),
    ]
    for uname, email, pw, dept in heads:
        if not db.query(User).filter(User.username == uname).first():
            db.add(User(username=uname, email=email, password_hash=hash_password(pw),
                        role=UserRole.DEPT_HEAD, department=dept, is_active=True, is_on_leave=False))

    # ---- OIC accounts from CSV ---- #
    oic_accounts = [
        ("oic_reyes_fin",       "reyes.fin@cpfi.com",        "oic1234", "Finance"),
        ("oic_santos_fin",      "santos.fin@cpfi.com",       "oic1234", "Finance"),
        ("oic_garcia_fin",      "garcia.fin@cpfi.com",       "oic1234", "Finance"),
        ("oic_cruz_hr",         "cruz.hr@cpfi.com",          "oic1234", "Human Resources"),
        ("oic_dela_hr",         "dela.hr@cpfi.com",          "oic1234", "Human Resources"),
        ("oic_lim_hr",          "lim.hr@cpfi.com",           "oic1234", "Human Resources"),
        ("oic_torres_log",      "torres.log@cpfi.com",       "oic1234", "Logistics"),
        ("oic_flores_log",      "flores.log@cpfi.com",       "oic1234", "Logistics"),
        ("oic_rivera_log",      "rivera.log@cpfi.com",       "oic1234", "Logistics"),
        ("oic_mendoza_emd",     "mendoza.emd@cpfi.com",      "oic1234", "EMD"),
        ("oic_aquino_emd",      "aquino.emd@cpfi.com",       "oic1234", "EMD"),
        ("oic_bautista_emd",    "bautista.emd@cpfi.com",     "oic1234", "EMD"),
        ("oic_villanueva_prod", "villanueva.prod@cpfi.com",  "oic1234", "Production"),
        ("oic_castillo_prod",   "castillo.prod@cpfi.com",    "oic1234", "Production"),
        ("oic_ramos_prod",      "ramos.prod@cpfi.com",       "oic1234", "Production"),
        ("oic_magno_qa",        "magno.qa@cpfi.com",         "oic1234", "Quality Assurance"),
        ("oic_ocampo_qa",       "ocampo.qa@cpfi.com",        "oic1234", "Quality Assurance"),
        ("oic_salazar_qa",      "salazar.qa@cpfi.com",       "oic1234", "Quality Assurance"),
        ("oic_navarro_fp",      "navarro.fp@cpfi.com",       "oic1234", "Fishmeal & Project"),
        ("oic_robles_fp",       "robles.fp@cpfi.com",        "oic1234", "Fishmeal & Project"),
        ("oic_pascual_fp",      "pascual.fp@cpfi.com",       "oic1234", "Fishmeal & Project"),
        ("oic_aguilar_ie",      "aguilar.ie@cpfi.com",       "oic1234", "Industrial Engineering"),
        ("oic_dela_ie",         "dela.ie@cpfi.com",          "oic1234", "Industrial Engineering"),
        ("oic_ferrer_ie",       "ferrer.ie@cpfi.com",        "oic1234", "Industrial Engineering"),
    ]
    for uname, email, pw, dept in oic_accounts:
        if not db.query(User).filter(User.username == uname).first():
            db.add(User(username=uname, email=email, password_hash=hash_password(pw),
                        role=UserRole.DEPT_OIC, department=dept, is_active=True, is_on_leave=False))

    # ---- Operational roles ---- #
    ops = [
        ("it",    "it@cpfi.local",    "it123",    "it"),
        ("nurse", "nurse@cpfi.local", "nurse123", "nurse"),
        ("guard", "guard@cpfi.local", "guard123", "guard"),
    ]
    for uname, email, pw, role in ops:
        if not db.query(User).filter(User.username == uname).first():
            db.add(User(username=uname, email=email, password_hash=hash_password(pw),
                        role=UserRole(role), department=None, is_active=True, is_on_leave=False))

    db.commit()
