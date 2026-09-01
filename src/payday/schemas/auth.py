from typing import Optional
from pydantic import BaseModel, Field, EmailStr, field_validator
from payday.core.security import normalize_cameroon_phone


class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120, examples=["Jean-Luc Kamdem"])
    phone_number: str = Field(..., examples=["+237699123456", "677889900"])
    email: Optional[EmailStr] = Field(None, examples=["jeanluc@example.cm"])
    password: str = Field(..., min_length=8, max_length=100, description="Password must be at least 8 characters")
    id_document_no: str = Field(..., min_length=5, max_length=50, description="National ID, Passport, or Residence Permit number", examples=["108273948"])
    id_document_type: str = Field(default="NATIONAL_ID", examples=["NATIONAL_ID", "PASSPORT"])

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return normalize_cameroon_phone(v)


class LoginRequest(BaseModel):
    phone_number: str = Field(..., examples=["+237699123456", "677889900"])
    password: str = Field(..., min_length=1)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return normalize_cameroon_phone(v)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    role: str
    has_pin: bool
    kyc_status: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class SetPinRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6, pattern=r"^\d{4,6}$", description="4 to 6 digit numerical PIN", examples=["1234"])
    password: str = Field(..., min_length=1, description="Current password for verification")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(...)
    new_password: str = Field(..., min_length=8)
