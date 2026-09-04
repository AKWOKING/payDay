from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from payday.models.wallet import WalletStatus


class WalletResponse(BaseModel):
    wallet_id: str
    user_id: str
    balance: Decimal
    locked_balance: Decimal
    available_balance: Decimal
    currency: str
    status: WalletStatus
    daily_limit: Decimal
    monthly_limit: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WalletBalanceResponse(BaseModel):
    wallet_id: str
    balance: Decimal
    locked_balance: Decimal
    available_balance: Decimal
    currency: str
    status: WalletStatus


class UpdateLimitsRequest(BaseModel):
    daily_limit: Decimal = Field(..., gt=0, examples=[1000000.00])
    monthly_limit: Decimal = Field(..., gt=0, examples=[10000000.00])


class WalletStatusUpdateRequest(BaseModel):
    status: WalletStatus = Field(..., examples=["FROZEN", "ACTIVE"])
    reason: str = Field(..., min_length=3, examples=["Suspicious account activity review"])
