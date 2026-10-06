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

    # Operator-side identifiers (M1 / A8). A `success=False` response can still
    # carry these — e.g. a status requery that is not yet conclusive — and the
    # caller must persist them when it can.
    provider_order_id: Optional[str] = None
    provider_notif_token: Optional[str] = None
    provider_txn_id: Optional[str] = None


class ProviderCallback(BaseModel):
    """An operator callback, translated into PayDay's vocabulary.

    Deliberately *not* the same thing as `WebhookCallbackPayload` (which is the
    internal shape used by the mock simulator). This is what a vendor actually
    sends, normalised:

      * MTN sends `{"externalId", "transactionStatus", "financialTransactionId",
        "amount", "currency", "payee"}` and signs nothing.
      * Orange sends `{"status", "notif_token", "txnid"}` and nothing else — no
        order id, no amount, no reference.

    `provider_status` is recorded for the audit trail. It is **never** used to
    move the ledger: the operator's status endpoint is re-queried for that.
    """

    transaction_id: Optional[str] = None       # our transaction id, when the vendor echoes it
    provider_status: Optional[str] = None      # raw vendor status text
    provider_txn_id: Optional[str] = None      # MTN financialTransactionId / Orange txnid
    notif_token: Optional[str] = None          # Orange: the per-order secret to compare
    reason: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


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
    async def query_status(
        self,
        channel_ref: str,
        tx_type: str = "DEPOSIT",
        order_id: Optional[str] = None,
        amount: Optional[Decimal] = None,
    ) -> ChannelResponse:
        """Query the operator's own status endpoint — the authoritative answer.

        `channel_ref` is what this codebase stores as `Transaction.external_ref`
        (MTN `X-Reference-Id`, Orange `pay_token`). Orange's documented status
        contract also needs the `order_id` and `amount` used at initiation, so
        they are passed explicitly rather than guessed.

        Contract for the result, relied on by the settlement path:
          * `status` is one of `PENDING`, `PROCESSING`, `SUCCESS`, `FAILED`;
          * a status of `SUCCESS`/`FAILED` means the operator gave a conclusive
            answer and the ledger may move;
          * anything else — including a not-yet-visible reference — means the
            caller must leave the transaction untouched.
        """
        pass

    @abstractmethod
    async def verify_webhook_signature(self, headers: Dict[str, str], body: bytes) -> bool:
        """Legacy signature check, used by the mock simulator path only.

        Neither operator signs production callbacks (MTN signs nothing; Orange
        sends a per-order `notif_token`), so live authenticity is established by
        `verify_callback_authenticity` plus the status requery — never here.
        """
        pass

    def parse_callback(self, body: Dict[str, Any]) -> Optional[ProviderCallback]:
        """Translate this vendor's callback body, or `None` if it is not one.

        Pure: no I/O, no state. Implemented per adapter.
        """
        return None

    def verify_callback_authenticity(
        self, callback: ProviderCallback, expected_notif_token: Optional[str]
    ) -> bool:
        """Whether this callback can be attributed to that transaction.

        MTN: nothing local to check — MTN signs nothing, so authenticity comes
        from re-querying the operator with credentials only we hold.
        Orange: the echoed `notif_token` must match the stored one, compared in
        constant time.
        """
        return False
