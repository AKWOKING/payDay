from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict
from payday.models.transaction import TransactionType, TransactionChannel, TransactionStatus
from payday.models.user import UserRole, UserStatus, KycStatus


class AdminTransactionItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class AdminTransactionListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[AdminTransactionItemResponse]


class ManualReversalRequest(BaseModel):
    reason: str
    admin_notes: Optional[str] = None


class PartnerStatementItem(BaseModel):
    external_ref: str
    amount: Decimal
    currency: str = "XAF"
    status: str # SUCCESS, FAILED
    channel: str # MTN, ORANGE
    settlement_date: Optional[str] = None


class ReconciliationRequest(BaseModel):
    channel: TransactionChannel
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    partner_records: Optional[List[PartnerStatementItem]] = None


class ReconciliationMismatchItem(BaseModel):
    mismatch_type: str # AMOUNT_MISMATCH, STATUS_MISMATCH, MISSING_IN_INTERNAL, MISSING_IN_PARTNER
    transaction_id: Optional[str] = None
    external_ref: Optional[str] = None
    internal_amount: Optional[Decimal] = None
    partner_amount: Optional[Decimal] = None
    internal_status: Optional[str] = None
    partner_status: Optional[str] = None
    description: str


class ReconciliationReportResponse(BaseModel):
    report_id: str
    channel: TransactionChannel
    generated_at: datetime
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    total_internal_transactions: int
    total_internal_volume: Decimal
    total_internal_fees: Decimal
    total_partner_transactions: int
    total_partner_volume: Decimal
    matched_count: int
    mismatches_count: int
    is_balanced: bool
    mismatches: List[ReconciliationMismatchItem]


class AuditLogItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_id: str
    actor_id: Optional[str] = None
    action: str
    entity_name: str
    entity_id: str
    old_state: Optional[Dict[str, Any]] = None
    new_state: Optional[Dict[str, Any]] = None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[AuditLogItemResponse]
