"""models.py – ORM models."""
import datetime as dt, enum
from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String, Text
from app.database import Base

class UserRole(str, enum.Enum):
    DEPT_HEAD = "dept_head"
    DEPT_OIC  = "dept_oic"
    GUARD     = "guard"
    IT        = "it"
    NURSE     = "nurse"

class GatepassStatus(str, enum.Enum):
    PENDING_NURSE = "pending_nurse"
    PENDING       = "pending"
    APPROVED      = "approved"
    DENIED        = "denied"
    COMPLETED     = "completed"
    CANCELLED     = "cancelled"

class ResetRequestStatus(str, enum.Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

DEPARTMENTS = [
    "Finance", "Industrial Engineering", "Human Resources",
    "Logistics", "Production", "Quality Assurance",
    "Fishmeal & Project", "EMD",
]

DEPT_ROLES = {"dept_head", "dept_oic"}


class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(80),  unique=True, nullable=False, index=True)
    email         = Column(String(150), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    role          = Column(Enum(UserRole), nullable=False)
    department    = Column(String(100), nullable=True)
    is_active     = Column(Boolean, default=True, nullable=False)
    is_on_leave   = Column(Boolean, default=False, nullable=False)
    leave_until   = Column(DateTime, nullable=True)  # auto-clear at 6 AM on this date
    created_at    = Column(DateTime, default=dt.datetime.now)
    updated_at    = Column(DateTime, default=dt.datetime.now, onupdate=dt.datetime.now)

    def as_dict(self):
        return {
            "id": self.id, "username": self.username, "email": self.email,
            "role": self.role.value, "department": self.department,
            "is_active": self.is_active,
            "is_on_leave": self.is_on_leave,
            "leave_until": self.leave_until.strftime("%Y-%m-%d") if self.leave_until else None,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


class Staff(Base):
    __tablename__ = "staff"
    id          = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String(50), unique=True, nullable=False, index=True)
    name        = Column(String(150), nullable=False)
    department  = Column(String(100), nullable=False)
    def as_dict(self):
        return {"id": self.id, "employee_id": self.employee_id,
                "name": self.name, "department": self.department}


class Gatepass(Base):
    __tablename__ = "gatepasses"
    id               = Column(Integer, primary_key=True, index=True)
    employee_id      = Column(String(50),  nullable=False, index=True)
    name             = Column(String(150), nullable=False)
    department       = Column(String(100), nullable=False)
    purpose          = Column(String(50),  nullable=False)
    return_time      = Column(String(50),  nullable=True)
    notes            = Column(Text,        nullable=True)
    diagnosis        = Column(Text,        nullable=True)
    recommendation   = Column(String(60),  nullable=True)
    status           = Column(Enum(GatepassStatus), default=GatepassStatus.PENDING, nullable=False, index=True)
    # Routing: dept name, "__admin__", or None (own dept)
    delegated_to     = Column(String(100), nullable=True)
    delegated_note   = Column(Text,        nullable=True)
    accepted_by_dept = Column(String(100), nullable=True)
    # Who submitted (staff / dept_oic)
    submitter_role   = Column(String(30),  nullable=True)
    # Exclusive visibility: if set, only this user_id can see/act on the request
    assigned_to_user_id  = Column(Integer, nullable=True, index=True)
    assigned_to_username = Column(String(80), nullable=True)
    # Approver display name
    decided_by_name  = Column(String(150), nullable=True)
    created_at       = Column(DateTime, default=dt.datetime.now)
    decided_at       = Column(DateTime, nullable=True)
    decided_by       = Column(String(80),  nullable=True)
    left_at          = Column(DateTime, nullable=True)
    nurse_decided_at = Column(DateTime, nullable=True)
    nurse_decided_by = Column(String(80),  nullable=True)
    nurse_status     = Column(String(20),  nullable=True)
    returned_at      = Column(DateTime, nullable=True)
    cancelled_at     = Column(DateTime, nullable=True)
    is_archived      = Column(Boolean,  default=False, nullable=False, index=True)
    archived_at      = Column(DateTime, nullable=True)

    def as_dict(self):
        def fmt(d): return d.strftime("%Y-%m-%d %H:%M:%S") if d else None
        return {
            "id": self.id, "employee_id": self.employee_id,
            "name": self.name, "department": self.department,
            "purpose": self.purpose, "return_time": self.return_time or "—",
            "notes": self.notes, "diagnosis": self.diagnosis, "recommendation": self.recommendation,
            "status": self.status.value,
            "delegated_to": self.delegated_to, "delegated_note": self.delegated_note,
            "accepted_by_dept": self.accepted_by_dept,
            "submitter_role": self.submitter_role,
            "assigned_to_user_id": self.assigned_to_user_id,
            "assigned_to_username": self.assigned_to_username,
            "decided_by_name": self.decided_by_name,
            "created_at": fmt(self.created_at), "decided_at": fmt(self.decided_at),
            "decided_by": self.decided_by,
            "left_at": fmt(self.left_at),
            "nurse_decided_at": fmt(self.nurse_decided_at),
            "nurse_decided_by": self.nurse_decided_by, "nurse_status": self.nurse_status,
            "returned_at": fmt(self.returned_at),
            "cancelled_at": fmt(self.cancelled_at),
            "is_archived": self.is_archived, "archived_at": fmt(self.archived_at),
        }


class Notification(Base):
    __tablename__ = "notifications"
    id          = Column(Integer, primary_key=True, index=True)
    message     = Column(Text, nullable=False)
    type        = Column(String(30), nullable=False)
    related_id  = Column(Integer, nullable=True)
    target_role = Column(String(30), nullable=True)
    target_dept = Column(String(100), nullable=True)
    target_user_id = Column(Integer, nullable=True)
    is_read     = Column(Boolean, default=False)
    created_at  = Column(DateTime, default=dt.datetime.now)

    def as_dict(self):
        return {
            "id": self.id, "message": self.message, "type": self.type,
            "related_id": self.related_id, "target_role": self.target_role,
            "target_dept": self.target_dept, "target_user_id": self.target_user_id,
            "is_read": self.is_read,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


class AuditLog(Base):
    __tablename__ = "audit_log"
    id           = Column(Integer, primary_key=True, index=True)
    action       = Column(String(50), nullable=False)
    details      = Column(Text, nullable=False)
    performed_by = Column(String(80), nullable=False)
    created_at   = Column(DateTime, default=dt.datetime.now)
    def as_dict(self):
        return {"id": self.id, "action": self.action, "details": self.details,
                "performed_by": self.performed_by,
                "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None}


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, nullable=False)
    token      = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used       = Column(Boolean, default=False)
    created_at = Column(DateTime, default=dt.datetime.now)


class GuardResetRequest(Base):
    __tablename__ = "guard_reset_requests"
    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, nullable=False)
    username      = Column(String(80),  nullable=False)
    email         = Column(String(150), nullable=False)
    status        = Column(Enum(ResetRequestStatus), default=ResetRequestStatus.PENDING)
    reviewed_by   = Column(String(80),  nullable=True)
    temp_password = Column(String(50),  nullable=True)
    created_at    = Column(DateTime, default=dt.datetime.now)
    reviewed_at   = Column(DateTime, nullable=True)
    def as_dict(self):
        return {
            "id": self.id, "user_id": self.user_id, "username": self.username,
            "email": self.email, "status": self.status.value,
            "reviewed_by": self.reviewed_by, "temp_password": self.temp_password,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "reviewed_at": self.reviewed_at.strftime("%Y-%m-%d %H:%M:%S") if self.reviewed_at else None,
        }


class AppSetting(Base):
    __tablename__ = "app_settings"
    id    = Column(Integer, primary_key=True, index=True)
    key   = Column(String(50), unique=True, nullable=False, index=True)
    value = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=dt.datetime.now, onupdate=dt.datetime.now)
