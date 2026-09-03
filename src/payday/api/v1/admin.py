from typing import List, Optional
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from payday.core.database import get_db
from payday.schemas.common import APIResponse, PaginatedResponse
from payday.schemas.user import UserResponse
from payday.schemas.wallet import UpdateLimitsRequest, WalletStatusUpdateRequest, WalletResponse
from payday.schemas.admin import (
    AdminTransactionListResponse,
    AdminTransactionItemResponse,
    ManualReversalRequest,
    ReconciliationRequest,
    ReconciliationReportResponse,
    AuditLogListResponse,
    AuditLogItemResponse,
)
from payday.services.wallet_engine import wallet_engine
from payday.services.audit_service import audit_service
from payday.services.reconciliation_service import reconciliation_service
from payday.services.transaction_manager import transaction_manager
from payday.api.deps import require_roles
from payday.models.user import User, UserRole, UserStatus
from payday.models.wallet import Wallet
from payday.models.transaction import Transaction, TransactionType, TransactionChannel, TransactionStatus
from payday.models.audit_log import AuditLog
from payday.core.exceptions import UserNotFoundError, WalletNotFoundError, PayDayException, InvalidStateTransitionError

router = APIRouter(prefix="/admin", tags=["Admin Back-Office"])


@router.get(
    "/users",
    response_model=APIResponse[PaginatedResponse[UserResponse]],
    summary="List All Users (Admin)",
    description="Returns paginated list of users with search and filter capabilities for the Angular Admin Portal.",
)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    role: Optional[UserRole] = None,
    current_admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.AUDITOR)),
    db: AsyncSession = Depends(get_db),
):
    query = select(User)
    if search:
        query = query.where(
            (User.full_name.ilike(f"%{search}%")) | (User.phone_number.ilike(f"%{search}%"))
        )
    if role:
        query = query.where(User.role == role)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    query = query.offset((page - 1) * page_size).limit(page_size).order_by(User.created_at.desc())
    result = await db.execute(query)
    users = result.scalars().all()

    items = [
        UserResponse(
            user_id=u.user_id,
            full_name=u.full_name,
            phone_number=u.phone_number,
            email=u.email,
            kyc_status=u.kyc_status,
            role=u.role,
            status=u.status,
            has_pin=bool(u.pin_hash),
            created_at=u.created_at,
            updated_at=u.updated_at,
        )
        for u in users
    ]

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    return APIResponse(
        success=True,
        data=PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        ),
    )


@router.post(
    "/users/{user_id}/status",
    response_model=APIResponse[dict],
    summary="Update User Account Status (Admin)",
    description="Allows administrator to suspend, activate, or close a user account.",
)
async def update_user_status(
    user_id: str,
    status_val: UserStatus,
    reason: str = Query(..., min_length=3),
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.user_id == user_id))
    user = result.scalars().first()
    if not user:
        raise UserNotFoundError()

    old_status = user.status.value
    user.status = status_val

    await audit_service.log_action(
        db=db,
        action=f"USER_STATUS_{status_val.value}",
        entity_name="User",
        entity_id=user_id,
        actor_id=current_admin.user_id,
        old_state={"status": old_status},
        new_state={"status": status_val.value, "reason": reason},
    )
    await db.commit()
    return APIResponse(
        success=True,
        message=f"User status updated from {old_status} to {status_val.value}",
        data={"user_id": user_id, "new_status": status_val.value},
    )


@router.post(
    "/wallets/{wallet_id}/status",
    response_model=APIResponse[WalletResponse],
    summary="Freeze or Unfreeze Wallet (Admin)",
    description="Allows compliance officers to freeze a wallet due to suspicious activities or unfreeze after review.",
)
async def update_wallet_status(
    wallet_id: str,
    req: WalletStatusUpdateRequest,
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    wallet = await wallet_engine.update_status(
        db=db,
        wallet_id=wallet_id,
        status=req.status,
        reason=req.reason,
        admin_id=current_admin.user_id,
    )
    return APIResponse(
        success=True,
        message=f"Wallet status updated to {req.status.value}",
        data=WalletResponse(
            wallet_id=wallet.wallet_id,
            user_id=wallet.user_id,
            balance=wallet.balance,
            locked_balance=wallet.locked_balance,
            available_balance=wallet.available_balance,
            currency=wallet.currency,
            status=wallet.status,
            daily_limit=wallet.daily_limit,
            monthly_limit=wallet.monthly_limit,
            created_at=wallet.created_at,
            updated_at=wallet.updated_at,
        ),
    )


@router.put(
    "/wallets/{wallet_id}/limits",
    response_model=APIResponse[WalletResponse],
    summary="Update Wallet Transaction Limits (Admin)",
    description="Updates daily and monthly transaction limits for a specific wallet.",
)
async def update_wallet_limits(
    wallet_id: str,
    req: UpdateLimitsRequest,
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    wallet = await wallet_engine.update_limits(
        db=db,
        wallet_id=wallet_id,
        daily_limit=req.daily_limit,
        monthly_limit=req.monthly_limit,
        admin_id=current_admin.user_id,
    )
    return APIResponse(
        success=True,
        message="Wallet transaction limits updated successfully",
        data=WalletResponse(
            wallet_id=wallet.wallet_id,
            user_id=wallet.user_id,
            balance=wallet.balance,
            locked_balance=wallet.locked_balance,
            available_balance=wallet.available_balance,
            currency=wallet.currency,
            status=wallet.status,
            daily_limit=wallet.daily_limit,
            monthly_limit=wallet.monthly_limit,
            created_at=wallet.created_at,
            updated_at=wallet.updated_at,
        ),
    )


@router.get(
    "/transactions",
    response_model=APIResponse[AdminTransactionListResponse],
    summary="List All Transactions (Admin / Auditor)",
    description="Queries all platform transactions across all users with multi-criteria filtering.",
)
async def list_admin_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tx_type: Optional[TransactionType] = None,
    channel: Optional[TransactionChannel] = None,
    status_filter: Optional[TransactionStatus] = Query(None, alias="status"),
    search: Optional[str] = None,
    current_admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.AUDITOR)),
    db: AsyncSession = Depends(get_db),
):
    query = select(Transaction)
    if tx_type:
        query = query.where(Transaction.type == tx_type)
    if channel:
        query = query.where(Transaction.channel == channel)
    if status_filter:
        query = query.where(Transaction.status == status_filter)
    if search:
        query = query.where(
            (Transaction.idempotency_key.ilike(f"%{search}%"))
            | (Transaction.external_ref.ilike(f"%{search}%"))
            | (Transaction.transaction_id == search)
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    query = query.offset((page - 1) * page_size).limit(page_size).order_by(Transaction.created_at.desc())
    items = (await db.execute(query)).scalars().all()

    return APIResponse(
        success=True,
        message="Transactions retrieved successfully",
        data=AdminTransactionListResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=[AdminTransactionItemResponse.model_validate(tx) for tx in items],
        ),
    )


@router.post(
    "/transactions/{transaction_id}/reverse",
    response_model=APIResponse[AdminTransactionItemResponse],
    summary="Manual Transaction Reversal (Admin)",
    description="Reverses a previously successful transaction, executing compensatory ledger debits or credits.",
)
async def reverse_transaction(
    transaction_id: str,
    req: ManualReversalRequest,
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Transaction).where(Transaction.transaction_id == transaction_id))
    tx = result.scalars().first()
    if not tx:
        raise PayDayException(status_code=404, detail="Transaction not found", code="TRANSACTION_NOT_FOUND")

    if tx.status != TransactionStatus.SUCCESS:
        raise InvalidStateTransitionError(current_status=tx.status.value, target_status=TransactionStatus.REVERSED.value)

    # Lock wallet
    wallet = await wallet_engine.get_wallet_with_lock(db, tx.wallet_id)

    # Apply compensatory ledger action
    if tx.type == TransactionType.DEPOSIT:
        # Deposit was credited to wallet -> Now debit wallet net amount
        credit_amount = tx.amount - tx.fee
        if wallet.balance < credit_amount:
            raise PayDayException(
                status_code=400,
                detail=f"Cannot reverse deposit: wallet balance ({wallet.balance} XAF) is less than reversal amount ({credit_amount} XAF).",
                code="INSUFFICIENT_FUNDS_FOR_REVERSAL",
            )
        wallet.balance = (wallet.balance - credit_amount).quantize(Decimal("0.01"))
    elif tx.type == TransactionType.WITHDRAW:
        # Withdrawal was debited -> Now refund amount + fee
        refund_amount = tx.amount + tx.fee
        wallet.balance = (wallet.balance + refund_amount).quantize(Decimal("0.01"))

    old_status = tx.status.value
    tx.status = TransactionStatus.REVERSED
    tx.failure_reason = f"Reversed by Admin ({current_admin.full_name}): {req.reason}"

    await audit_service.log_action(
        db=db,
        action="TRANSACTION_REVERSED",
        entity_name="Transaction",
        entity_id=tx.transaction_id,
        actor_id=current_admin.user_id,
        old_state={"status": old_status},
        new_state={
            "status": TransactionStatus.REVERSED.value,
            "reason": req.reason,
            "admin_notes": req.admin_notes,
            "new_balance": str(wallet.balance),
        },
    )

    await db.commit()
    await db.refresh(tx)

    return APIResponse(
        success=True,
        message=f"Transaction {transaction_id} successfully reversed.",
        data=AdminTransactionItemResponse.model_validate(tx),
    )


@router.post(
    "/reconcile",
    response_model=APIResponse[ReconciliationReportResponse],
    summary="Run Channel Settlement Reconciliation (Admin / Auditor)",
    description="Executes automated reconciliation comparing internal ledger transactions against external partner settlement records.",
)
async def reconcile_channel(
    req: ReconciliationRequest,
    current_admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.AUDITOR)),
    db: AsyncSession = Depends(get_db),
):
    report = await reconciliation_service.run_reconciliation(db=db, req=req)

    await audit_service.log_action(
        db=db,
        action="CHANNEL_RECONCILIATION_RUN",
        entity_name="ReconciliationReport",
        entity_id=report.report_id,
        actor_id=current_admin.user_id,
        new_state={
            "channel": req.channel.value,
            "matched_count": report.matched_count,
            "mismatches_count": report.mismatches_count,
            "is_balanced": report.is_balanced,
        },
    )
    await db.commit()

    return APIResponse(
        success=True,
        message=f"Reconciliation completed for {req.channel.value}. Balanced: {report.is_balanced}",
        data=report,
    )


@router.get(
    "/audit-logs",
    response_model=APIResponse[AuditLogListResponse],
    summary="Get System Audit Logs (Admin / Auditor)",
    description="Queries immutable system-wide audit logs with actor and entity filters.",
)
async def get_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: Optional[str] = None,
    entity_name: Optional[str] = None,
    current_admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.AUDITOR)),
    db: AsyncSession = Depends(get_db),
):
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action.ilike(f"%{action}%"))
    if entity_name:
        query = query.where(AuditLog.entity_name == entity_name)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    query = query.offset((page - 1) * page_size).limit(page_size).order_by(AuditLog.created_at.desc())
    items = (await db.execute(query)).scalars().all()

    return APIResponse(
        success=True,
        message="Audit logs retrieved successfully",
        data=AuditLogListResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=[AuditLogItemResponse.model_validate(log) for log in items],
        ),
    )
