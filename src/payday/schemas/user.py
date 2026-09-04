from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict
from payday.models.user import KycStatus, UserRole, UserStatus


class UserResponse(BaseModel):
    user_id: str
    full_name: str
    phone_number: str
    email: Optional[EmailStr] = None
    kyc_status: KycStatus
    role: UserRole
    status: UserStatus
    has_pin: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
