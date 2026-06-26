"""schemas.py – Pydantic request/response models."""
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import re

ALL_ROLES = "^(dept_head|dept_oic|guard|it|nurse)$"

class GatepassCreate(BaseModel):
    token: str
    employee_id: str
    purpose: str
    return_time: Optional[str] = None
    notes: Optional[str] = None
    diagnosis: Optional[str] = None
    qr_password: Optional[str] = None
    # Routing for staff: assigned to a specific approver user_id
    assigned_to_user_id: Optional[int] = None
    # Routing for OIC self-submission
    submitter_role: Optional[str] = None

class OicGatepassCreate(BaseModel):
    """OIC submits their own gatepass (authenticated via session)."""
    purpose: str
    return_time: Optional[str] = None
    notes: Optional[str] = None
    diagnosis: Optional[str] = None
    # Null = auto-assign to own dept head; set = explicit delegate chosen
    assigned_to_user_id: Optional[int] = None

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=80)
    email: str = Field(..., max_length=150)
    password: str = Field(..., min_length=6)
    role: str = Field(..., pattern=ALL_ROLES)
    department: Optional[str] = None
    @field_validator("email")
    @classmethod
    def ve(cls, v):
        if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", v): raise ValueError("Invalid email")
        return v.lower()

class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=80)
    email: Optional[str] = None
    role: Optional[str] = Field(None, pattern=ALL_ROLES)
    department: Optional[str] = None
    is_active: Optional[bool] = None

class PasswordReset(BaseModel):
    password: str = Field(..., min_length=6)
