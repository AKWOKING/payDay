import enum
from sqlalchemy import Column, String, Enum, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from payday.models.base import TimeStampedModel, generate_uuid


class ChannelProvider(str, enum.Enum):
    MTN = "MTN"
    ORANGE = "ORANGE"
    UBA = "UBA"


class LinkedExternalAccount(TimeStampedModel):
    __tablename__ = "linked_external_accounts"

    linked_account_id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(Enum(ChannelProvider), nullable=False, index=True)
    account_identifier = Column(String(50), nullable=False) # e.g. MSISDN or Bank Account Number
    is_verified = Column(Boolean, nullable=False, default=False)
    is_default = Column(Boolean, nullable=False, default=False)

    # Relationships
    user = relationship("User", back_populates="linked_accounts")
    transactions = relationship("Transaction", back_populates="linked_account")

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "account_identifier", name="uq_user_provider_account"),
    )
