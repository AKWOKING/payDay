from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from payday.core.database import get_db
from payday.schemas.common import APIResponse, PaginatedResponse
from payday.schemas.transaction import (
    DepositInitiateRequest,
    WithdrawInitiateRequest,
    TransactionResponse,
    TransactionReceiptResponse,
)
from payday.services.transaction_manager import transaction_manager
from payday.services.wallet_engine import wallet_engine
from payday.api.deps import get_current_user
from payday.models.user import User
from payday.models.transaction import Transaction, TransactionType, TransactionChannel, TransactionStatus

router = APIRouter(prefix="/wallet", tags=["Transactions & Money Movement"])


@router.post(
    "/deposit",
    response_model=APIResponse[TransactionResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Initiate Wallet Deposit (MTN MoMo / Orange Money)",
    description="Initiates a collection request against the customer's mobile money account. Customer receives a USSD prompt on their phone to authorize the transaction.",
)
async def deposit(
    req: DepositInitiateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tx = await transaction_manager.initiate_deposit(db, current_user, req)
    msg = "Deposit initiated. Please authorize the prompt on your mobile phone." if tx.status == TransactionStatus.PROCESSING else (
        "Deposit completed successfully." if tx.status == TransactionStatus.SUCCESS else f"Deposit failed: {tx.failure_reason}"
    )
    return APIResponse(
        success=(tx.status in (TransactionStatus.SUCCESS, TransactionStatus.PROCESSING, TransactionStatus.PENDING)),
        message=msg,
        data=TransactionResponse.model_validate(tx),
    )


@router.post(
    "/withdraw",
    response_model=APIResponse[TransactionResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Initiate Wallet Withdrawal (MTN MoMo / Orange Money)",
    description="Places a hold on wallet funds and dispatches payout/transfer to the destination mobile money account. Requires 4-6 digit transaction PIN.",
)
async def withdraw(
    req: WithdrawInitiateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tx = await transaction_manager.initiate_withdrawal(db, current_user, req)
    msg = "Withdrawal is processing." if tx.status == TransactionStatus.PROCESSING else (
        "Withdrawal completed successfully." if tx.status == TransactionStatus.SUCCESS else f"Withdrawal failed: {tx.failure_reason}"
    )
    return APIResponse(
        success=(tx.status in (TransactionStatus.SUCCESS, TransactionStatus.PROCESSING, TransactionStatus.PENDING)),
        message=msg,
        data=TransactionResponse.model_validate(tx),
    )


@router.get(
    "/transactions",
    response_model=APIResponse[PaginatedResponse[TransactionResponse]],
    summary="Paginated Transaction History",
    description="Returns chronological history of deposits and withdrawals with optional filters for channel, status, and type.",
)
async def list_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tx_type: Optional[TransactionType] = None,
    channel: Optional[TransactionChannel] = None,
    tx_status: Optional[TransactionStatus] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wallet = await wallet_engine.get_wallet_by_user_id(db, current_user.user_id)
    query = select(Transaction).where(Transaction.wallet_id == wallet.wallet_id)

    if tx_type:
        query = query.where(Transaction.type == tx_type)
    if channel:
        query = query.where(Transaction.channel == channel)
    if tx_status:
        query = query.where(Transaction.status == tx_status)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    # Apply pagination & sorting (newest first)
    query = query.order_by(Transaction.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    transactions = result.scalars().all()

    items = [TransactionResponse.model_validate(tx) for tx in transactions]
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


@router.get(
    "/transactions/{transaction_id}",
    response_model=APIResponse[TransactionReceiptResponse],
    summary="Get Transaction Receipt",
    description="Returns detailed receipt for a specific deposit or withdrawal.",
)
async def get_transaction_receipt(
    transaction_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    receipt = await transaction_manager.get_receipt(db, current_user, transaction_id)
    return APIResponse(success=True, data=receipt)
