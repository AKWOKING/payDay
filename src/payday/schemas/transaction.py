from decimal import Decimal
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict
from payday.models.transaction import TransactionType, TransactionChannel, TransactionStatus
from payday.core.security import normalize_cameroon_phone


class DepositInitiateRequest(BaseModel):
    channel: TransactionChannel = Field(..., examples=["MTN", "ORANGE"])
    amount: Decimal = Field(..., gt=0, examples=[5000.00], description="Amount in XAF to deposit")
    phone_number: Optional[str] = Field(None, description="MSISDN to debit (defaults to user's registered number)", examples=["+237677112233"])
    idempotency_key: Optional[str] = Field(None, description="Unique client UUID to prevent duplicate submissions")
    pin: Optional[str] = Field(None, min_length=4, max_length=6, description="Optional transaction PIN for high-value deposit confirmation")

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v:
            return normalize_cameroon_phone(v)
        return v


class WithdrawInitiateRequest(BaseModel):
    channel: TransactionChannel = Field(..., examples=["MTN", "ORANGE"])
    amount: Decimal = Field(..., gt=0, examples=[10000.00], description="Amount in XAF to withdraw")
    destination_phone: str = Field(..., description="Destination MSISDN", examples=["+237677112233"])
    pin: str = Field(..., min_length=4, max_length=6, description="Mandatory 4-6 digit numeric transaction PIN", examples=["1234"])
    idempotency_key: Optional[str] = Field(None, description="Unique client UUID to prevent duplicate submissions")

    @field_validator("destination_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return normalize_cameroon_phone(v)


class TransactionResponse(BaseModel):
    transaction_id: str
    idempotency_key: str
    wallet_id: str
    type: TransactionType
    channel: TransactionChannel
    amount: Decimal
    fee: Decimal
    net_amount: Decimal
    status: TransactionStatus
    external_ref: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TransactionReceiptResponse(BaseModel):
    transaction_id: str
    idempotency_key: str
    user_name: str
    user_phone: str
    type: TransactionType
    channel: TransactionChannel
    currency: str = "XAF"
    amount: Decimal
    fee: Decimal
    total_charged: Decimal
    net_credited: Decimal
    status: TransactionStatus
    external_ref: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    message: str


class WebhookCallbackPayload(BaseModel):
    transaction_id: Optional[str] = None
    external_ref: str = Field(..., examples=["MTN-MOMO-12345678"])
    status: str = Field(..., examples=["SUCCESSFUL", "FAILED", "REJECTED"])
    amount: Optional[Decimal] = None
    currency: Optional[str] = "XAF"
    reason: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
