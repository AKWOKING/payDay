import enum
from decimal import Decimal
from sqlalchemy import Column, String, Enum, Numeric, ForeignKey, DateTime, JSON, CheckConstraint
from sqlalchemy.orm import relationship
from payday.models.base import TimeStampedModel, generate_uuid


class TransactionType(str, enum.Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"


class TransactionChannel(str, enum.Enum):
    MTN = "MTN"
    ORANGE = "ORANGE"
    UBA = "UBA"


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

    __table_args__ = (
        CheckConstraint("amount > 0.00", name="chk_tx_positive_amount"),
        CheckConstraint("fee >= 0.00", name="chk_tx_positive_fee"),
    )
