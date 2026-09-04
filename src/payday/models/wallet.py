import enum
from decimal import Decimal
from sqlalchemy import Column, String, Enum, Numeric, ForeignKey, CheckConstraint
from sqlalchemy.orm import relationship
from payday.models.base import TimeStampedModel, generate_uuid


class WalletStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class Wallet(TimeStampedModel):
    __tablename__ = "wallets"

    wallet_id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="RESTRICT"), unique=True, nullable=False, index=True)
    
    # Financial fields with 2 decimal precision
    balance = Column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    locked_balance = Column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    currency = Column(String(3), nullable=False, default="XAF")
    
    status = Column(Enum(WalletStatus), nullable=False, default=WalletStatus.ACTIVE, index=True)
    daily_limit = Column(Numeric(14, 2), nullable=False, default=Decimal("500000.00"))
    monthly_limit = Column(Numeric(14, 2), nullable=False, default=Decimal("5000000.00"))

    # Relationships
    user = relationship("User", back_populates="wallet")
    transactions = relationship("Transaction", back_populates="wallet", lazy="dynamic")

    __table_args__ = (
        CheckConstraint("balance >= 0.00", name="chk_wallet_positive_balance"),
        CheckConstraint("locked_balance >= 0.00", name="chk_wallet_positive_locked_balance"),
    )

    @property
    def available_balance(self) -> Decimal:
        return self.balance - self.locked_balance
