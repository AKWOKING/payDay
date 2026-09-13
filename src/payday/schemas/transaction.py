from decimal import Decimal
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict
from payday.models.transaction import TransactionType, TransactionChannel, TransactionStatus
from payday.core.security import normalize_cameroon_phone


class DepositInitiateRequest(BaseModel):
    channel: TransactionChannel = Field(..., examples=["MTN", "ORANGE"])
    # `multiple_of=1` because XAF has no minor unit (ISO 4217 exponent 0): there
    # is no centime. Without this, 1000.55 was a valid deposit request, MTN was
    # sent "1000.55", Orange truncated it to 1000, and the ledger credited
    # 995.55 — see docs/MVP_EXECUTION_ROADMAP.md LB-9.
    amount: Decimal = Field(
        ...,
        gt=0,
        multiple_of=Decimal("1"),
        examples=[5000],
        description="Amount in XAF to deposit. Whole francs only: XAF has no minor unit.",
    )
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
    amount: Decimal = Field(
        ...,
        gt=0,
        multiple_of=Decimal("1"),
        examples=[10000],
        description="Amount in XAF to withdraw. Whole francs only: XAF has no minor unit.",
    )
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
