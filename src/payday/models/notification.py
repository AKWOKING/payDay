import enum
from sqlalchemy import Column, String, Enum, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from payday.models.base import TimeStampedModel, generate_uuid


class NotificationChannel(str, enum.Enum):
    SMS = "SMS"
    PUSH = "PUSH"


class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class Notification(TimeStampedModel):
    __tablename__ = "notifications"

    notification_id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    transaction_id = Column(String(36), ForeignKey("transactions.transaction_id", ondelete="SET NULL"), nullable=True, index=True)
    
    channel = Column(Enum(NotificationChannel), nullable=False)
    recipient = Column(String(100), nullable=False) # Phone MSISDN or FCM Device Token
    message = Column(String(500), nullable=False)
    status = Column(Enum(NotificationStatus), nullable=False, default=NotificationStatus.PENDING, index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="notifications")
    transaction = relationship("Transaction", back_populates="notifications")
