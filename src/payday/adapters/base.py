from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ChannelDepositRequest(BaseModel):
    transaction_id: str
    phone_number: str # Customer MSISDN e.g. +2376XXXXXXXX
    amount: Decimal
    currency: str = "XAF"
    description: str
    payer_message: Optional[str] = "PayDay Deposit"


class ChannelWithdrawalRequest(BaseModel):
    transaction_id: str
    destination_phone: str # Destination MSISDN
    amount: Decimal
    currency: str = "XAF"
    description: str
    payee_note: Optional[str] = "PayDay Withdrawal"


class ChannelResponse(BaseModel):
    success: bool
    channel_ref: Optional[str] = None
    status: str = Field(..., description="PENDING, PROCESSING, SUCCESS, FAILED")
    message: str = "Operation acknowledged by provider"
    raw_response: Dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None


class PaymentChannelAdapter(ABC):
    """Abstract Port Interface for External Payment Channels."""

    @abstractmethod
    async def initiate_deposit(self, req: ChannelDepositRequest) -> ChannelResponse:
        """Initiates cash-in / collection from external mobile money account."""
        pass

    @abstractmethod
    async def initiate_withdrawal(self, req: ChannelWithdrawalRequest) -> ChannelResponse:
        """Initiates cash-out / disbursement to external mobile money account."""
        pass

    @abstractmethod
    async def query_status(self, channel_ref: str, tx_type: str = "DEPOSIT") -> ChannelResponse:
        """Queries external transaction status for fallback polling & reconciliation."""
        pass

    @abstractmethod
    async def verify_webhook_signature(self, headers: Dict[str, str], body: bytes) -> bool:
        """Verifies cryptographic signature / token of incoming partner webhook callback."""
        pass
