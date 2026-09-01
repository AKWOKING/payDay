from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from payday.core.database import get_db
from payday.schemas.common import APIResponse, PaginatedResponse
from payday.schemas.user import UserResponse
from payday.schemas.wallet import UpdateLimitsRequest, WalletStatusUpdateRequest, WalletResponse
from payday.services.wallet_engine import wallet_engine
from payday.services.audit_service import audit_service
from payday.api.deps import require_roles
from payday.models.user import User, UserRole, UserStatus
from payday.models.wallet import Wallet
from payday.core.exceptions import UserNotFoundError, WalletNotFoundError

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
