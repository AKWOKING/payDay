from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from payday.core.database import get_db
from payday.schemas.common import APIResponse
from payday.schemas.wallet import WalletResponse, WalletBalanceResponse
from payday.schemas.linked_account import LinkAccountRequest, LinkedAccountResponse
from payday.services.wallet_engine import wallet_engine
from payday.api.deps import get_current_user
from payday.models.user import User
from payday.models.linked_account import LinkedExternalAccount

router = APIRouter(prefix="/wallet", tags=["Wallet & Accounts"])


@router.get(
    "/balance",
    response_model=APIResponse[WalletBalanceResponse],
    summary="Get Wallet Balance",
    description="Returns available balance, locked balance (funds held for pending operations), currency, and wallet status.",
)
async def get_balance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wallet = await wallet_engine.get_wallet_by_user_id(db, current_user.user_id)
    return APIResponse(
        success=True,
        data=WalletBalanceResponse(
            wallet_id=wallet.wallet_id,
            balance=wallet.balance,
            locked_balance=wallet.locked_balance,
            available_balance=wallet.available_balance,
            currency=wallet.currency,
            status=wallet.status,
        ),
    )


@router.get(
    "/me",
    response_model=APIResponse[WalletResponse],
    summary="Get Full Wallet Details",
    description="Returns comprehensive wallet metadata including daily and monthly limits.",
)
async def get_wallet_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wallet = await wallet_engine.get_wallet_by_user_id(db, current_user.user_id)
    return APIResponse(
        success=True,
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
    "/linked-accounts",
    response_model=APIResponse[List[LinkedAccountResponse]],
    summary="List Linked External Accounts",
    description="Lists all external mobile money numbers or bank accounts linked to the user.",
)
async def get_linked_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LinkedExternalAccount).where(LinkedExternalAccount.user_id == current_user.user_id)
    )
    accounts = result.scalars().all()
    data = [
        LinkedAccountResponse(
            linked_account_id=acc.linked_account_id,
            user_id=acc.user_id,
            provider=acc.provider,
            account_identifier=acc.account_identifier,
            is_verified=acc.is_verified,
            is_default=acc.is_default,
            created_at=acc.created_at,
        )
        for acc in accounts
    ]
    return APIResponse(success=True, data=data)


@router.post(
    "/linked-accounts",
    response_model=APIResponse[LinkedAccountResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Link External Account (MTN / Orange / UBA)",
    description="Links an external Mobile Money phone number or UBA bank account to the user's wallet.",
)
async def link_external_account(
    req: LinkAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check if this account identifier is already linked for this user
    result = await db.execute(
        select(LinkedExternalAccount).where(
            LinkedExternalAccount.user_id == current_user.user_id,
            LinkedExternalAccount.provider == req.provider,
            LinkedExternalAccount.account_identifier == req.account_identifier,
        )
    )
    existing = result.scalars().first()
    if existing:
        return APIResponse(
            success=True,
            message="Account is already linked",
            data=LinkedAccountResponse(
                linked_account_id=existing.linked_account_id,
                user_id=existing.user_id,
                provider=existing.provider,
                account_identifier=existing.account_identifier,
                is_verified=existing.is_verified,
                is_default=existing.is_default,
                created_at=existing.created_at,
            ),
        )

    linked = LinkedExternalAccount(
        user_id=current_user.user_id,
        provider=req.provider,
        account_identifier=req.account_identifier,
        is_verified=True,  # Mobile money ownership verified via PIN/OTP in transaction flow
        is_default=req.is_default,
    )
    db.add(linked)
    await db.commit()
    await db.refresh(linked)

    return APIResponse(
        success=True,
        message=f"{req.provider.value} account linked successfully",
        data=LinkedAccountResponse(
            linked_account_id=linked.linked_account_id,
            user_id=linked.user_id,
            provider=linked.provider,
            account_identifier=linked.account_identifier,
            is_verified=linked.is_verified,
            is_default=linked.is_default,
            created_at=linked.created_at,
        ),
    )
