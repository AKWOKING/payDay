from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict
from payday.models.linked_account import ChannelProvider
from payday.core.security import normalize_cameroon_phone


class LinkAccountRequest(BaseModel):
    provider: ChannelProvider = Field(..., examples=["MTN", "ORANGE", "UBA"])
    account_identifier: str = Field(..., min_length=4, max_length=50, examples=["+237677112233", "UBA-10029384"])
    is_default: bool = Field(default=False)

    @field_validator("account_identifier")
    @classmethod
    def validate_identifier(cls, v: str, info) -> str:
        provider = info.data.get("provider")
        if provider in (ChannelProvider.MTN, ChannelProvider.ORANGE):
            return normalize_cameroon_phone(v)
        return v.strip()


class LinkedAccountResponse(BaseModel):
    linked_account_id: str
    user_id: str
    provider: ChannelProvider
    account_identifier: str
    is_verified: bool
    is_default: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
