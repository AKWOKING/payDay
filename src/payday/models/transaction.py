import enum
from decimal import Decimal
from sqlalchemy import Column, String, Enum, Numeric, ForeignKey, DateTime, JSON, CheckConstraint
from sqlalchemy.orm import relationship
from payday.models.base import TimeStampedModel, generate_uuid


class TransactionType(str, enum.Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"
    #: PayDay wallet -> PayDay wallet. No external network is involved, so the
    #: movement is final the moment it is written (M1 / P2P).
    TRANSFER = "TRANSFER"


class TransactionChannel(str, enum.Enum):
    MTN = "MTN"
    ORANGE = "ORANGE"
    UBA = "UBA"
    #: Internal channel: the counterparty is another PayDay wallet, not an
    #: operator. Kept as a channel (rather than a null) so reporting and filters
    #: have one field to reason about.
    PAYDAY = "PAYDAY"


class TransactionDirection(str, enum.Enum):
    """Which way the money went, from the perspective of the owning wallet.

    Needed because `type` can no longer carry the sign: two legs of one internal
    transfer are both `TRANSFER`, and `amount` is constrained to be positive. It
    is also what lets a history response show a signed amount without the client
    inferring it from the transaction type.
    """

    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


def derive_direction(context) -> TransactionDirection:
    """Default `direction` from `type` for movements where it is unambiguous.

    Deposits credit and withdrawals debit, so requiring every existing call site
    to restate that would be noise. A `TRANSFER` leg, however, *must* say which
    side it is: guessing here would silently mislabel half of every transfer, so
    this raises instead. (Transfers always set `direction` explicitly.)
    """
    params = context.get_current_parameters()
    tx_type = params.get("type")
    if tx_type in (TransactionType.DEPOSIT, "DEPOSIT"):
        return TransactionDirection.CREDIT
    if tx_type in (TransactionType.WITHDRAW, "WITHDRAW"):
        return TransactionDirection.DEBIT
    raise ValueError(
        "Transaction.direction must be set explicitly for "
        f"type={tx_type!r}: it cannot be derived."
    )


class TransactionStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class Transaction(TimeStampedModel):
    __tablename__ = "transactions"

    transaction_id = Column(String(36), primary_key=True, default=generate_uuid)
    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    
    wallet_id = Column(String(36), ForeignKey("wallets.wallet_id", ondelete="RESTRICT"), nullable=False, index=True)
    linked_account_id = Column(String(36), ForeignKey("linked_external_accounts.linked_account_id", ondelete="SET NULL"), nullable=True, index=True)
    
    type = Column(Enum(TransactionType), nullable=False, index=True)
    channel = Column(Enum(TransactionChannel), nullable=False, index=True)
    
    amount = Column(Numeric(14, 2), nullable=False)
    fee = Column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    net_amount = Column(Numeric(14, 2), nullable=False)
    
    status = Column(Enum(TransactionStatus), nullable=False, default=TransactionStatus.PENDING, index=True)
    external_ref = Column(String(100), nullable=True, index=True)
    failure_reason = Column(String(255), nullable=True)

    #: CREDIT or DEBIT, from this wallet's point of view. Derived from `type`
    #: where that is unambiguous (see `derive_direction`).
    direction = Column(
        Enum(TransactionDirection), nullable=False, default=derive_direction
    )

    #: Internal transfers only. Both legs share one `transfer_group_id`, which is
    #: what makes conservation checkable (sum of the group is zero) rather than
    #: asserted.
    transfer_group_id = Column(String(36), nullable=True, index=True)
    counterparty_wallet_id = Column(String(36), nullable=True, index=True)
    #: Masked for display ("+2376•••233"). The full number is never returned in a
    #: list response; support resolves the real one from the wallet id.
    counterparty_msisdn_masked = Column(String(20), nullable=True)
    
    # Operator-side identifiers (M1 / A8). Written at initiation, used to match a
    # callback and to re-query the operator's own status endpoint -- the ledger
    # only moves on that answer, never on the callback body.
    #   MTN:    provider_txn_id = financialTransactionId (external_ref holds the X-Reference-Id)
    #   Orange: provider_order_id + provider_notif_token (external_ref holds the pay_token)
    provider_order_id = Column(String(100), nullable=True)
    provider_notif_token = Column(String(128), nullable=True, index=True)
    provider_txn_id = Column(String(100), nullable=True, index=True)

    extra_data = Column(JSON, nullable=True, default=dict)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    wallet = relationship("Wallet", back_populates="transactions")
    linked_account = relationship("LinkedExternalAccount", back_populates="transactions")
    notifications = relationship("Notification", back_populates="transaction")

    @property
    def internal(self) -> bool:
        """True when this movement never left PayDay.

        Derived rather than stored: it is exactly "the counterparty was another
        PayDay wallet", which the channel already says. A client uses this to
        render "sent instantly" instead of "processing with MTN".
        """
        return self.channel == TransactionChannel.PAYDAY

    __table_args__ = (
        CheckConstraint("amount > 0.00", name="chk_tx_positive_amount"),
        CheckConstraint("fee >= 0.00", name="chk_tx_positive_fee"),
    )
