from sqlalchemy import Column, String, ForeignKey, JSON
from payday.models.base import TimeStampedModel, generate_uuid


class AuditLog(TimeStampedModel):
    __tablename__ = "audit_logs"

    log_id = Column(String(36), primary_key=True, default=generate_uuid)
    actor_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(50), nullable=False, index=True) # e.g. USER_REGISTER, KYC_SUBMIT, WALLET_FREEZE
    entity_name = Column(String(50), nullable=False) # e.g. User, Wallet, Transaction
    entity_id = Column(String(36), nullable=False, index=True)
    old_state = Column(JSON, nullable=True)
    new_state = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
