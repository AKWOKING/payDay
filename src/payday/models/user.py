import enum
from sqlalchemy import Column, String, Enum, LargeBinary
from sqlalchemy.orm import relationship
from payday.models.base import TimeStampedModel, generate_uuid


class KycStatus(str, enum.Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class UserRole(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    ADMIN = "ADMIN"
    AUDITOR = "AUDITOR"


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class User(TimeStampedModel):
    __tablename__ = "users"

    user_id = Column(String(36), primary_key=True, default=generate_uuid)
    full_name = Column(String(120), nullable=False)
    phone_number = Column(String(20), unique=True, nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    pin_hash = Column(String(255), nullable=True)
    
    # KYC Details (Encrypted PII at rest)
    id_document_no_encrypted = Column(LargeBinary, nullable=True)
    id_document_type = Column(String(30), nullable=False, default="NATIONAL_ID")
    kyc_status = Column(Enum(KycStatus), nullable=False, default=KycStatus.PENDING, index=True)
    
    # Roles & Lifecycle
    role = Column(Enum(UserRole), nullable=False, default=UserRole.CUSTOMER, index=True)
    status = Column(Enum(UserStatus), nullable=False, default=UserStatus.ACTIVE, index=True)

    # Relationships
    wallet = relationship("Wallet", back_populates="user", uselist=False, cascade="all, delete-orphan")
    linked_accounts = relationship("LinkedExternalAccount", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
