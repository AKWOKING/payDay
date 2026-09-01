from decimal import Decimal
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from payday.core.config import settings
from payday.models.wallet import Wallet, WalletStatus
from payday.models.transaction import TransactionType
from payday.core.exceptions import (
    WalletNotFoundError,
    WalletFrozenError,
    InsufficientFundsError,
    DailyLimitExceededError,
    MonthlyLimitExceededError,
)
from payday.services.audit_service import audit_service


class WalletEngine:
    """
    The Wallet Engine is the sole financial authority permitted to alter
    wallet balances, place holds, and enforce transaction ceilings.
    """

    @staticmethod
    def calculate_fee(tx_type: TransactionType, amount: Decimal) -> Decimal:
        """Computes PayDay platform fee based on transaction type and amount."""
        if tx_type == TransactionType.DEPOSIT:
            fee_pct = Decimal(str(settings.DEFAULT_DEPOSIT_FEE_PERCENTAGE))
        else:
            fee_pct = Decimal(str(settings.DEFAULT_WITHDRAW_FEE_PERCENTAGE))
        
        calculated_fee = (amount * fee_pct).quantize(Decimal("0.01"))
        min_fee = Decimal(str(settings.MIN_FEE_AMOUNT))
        return max(calculated_fee, min_fee)

    @staticmethod
    async def get_wallet_by_user_id(db: AsyncSession, user_id: str) -> Wallet:
        result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
        wallet = result.scalars().first()
        if not wallet:
            raise WalletNotFoundError()
        return wallet

    @staticmethod
    async def get_wallet_with_lock(
        db: AsyncSession,
        wallet_id: str,
        require_active: bool = True,
    ) -> Wallet:
        """
        Acquires a pessimistic row-level lock (SELECT FOR UPDATE) on the wallet.
        On SQLite, standard select is performed.
        """
        query = select(Wallet).where(Wallet.wallet_id == wallet_id)
        if not settings.DATABASE_URL.startswith("sqlite"):
            query = query.with_for_update()

        result = await db.execute(query)
        wallet = result.scalars().first()
        if not wallet:
            raise WalletNotFoundError()

        if require_active and wallet.status != WalletStatus.ACTIVE:
            raise WalletFrozenError(f"Wallet is currently in {wallet.status.value} status.")

        return wallet

    @staticmethod
    async def validate_withdrawal_capacity(wallet: Wallet, amount: Decimal, fee: Decimal) -> None:
        """Verifies that the wallet has sufficient available balance and complies with limits."""
        total_required = amount + fee
        available = wallet.balance - wallet.locked_balance

        if available < total_required:
            raise InsufficientFundsError(available=float(available), required=float(total_required))

        if amount > wallet.daily_limit:
            raise DailyLimitExceededError(
                limit=float(wallet.daily_limit),
                current_total=0.0,
                requested=float(amount)
            )

    @staticmethod
    async def hold_funds(
        db: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        fee: Decimal,
        transaction_id: str,
    ) -> Wallet:
        """
        Places a hold on funds during withdrawal processing.
        Increments locked_balance by (amount + fee).
        """
        total_hold = amount + fee
        await WalletEngine.validate_withdrawal_capacity(wallet, amount, fee)

        wallet.locked_balance = (wallet.locked_balance + total_hold).quantize(Decimal("0.01"))
        await audit_service.log_action(
            db=db,
            action="FUNDS_HELD",
            entity_name="Wallet",
            entity_id=wallet.wallet_id,
            actor_id=wallet.user_id,
            new_state={
                "transaction_id": transaction_id,
                "amount": str(amount),
                "fee": str(fee),
                "locked_balance": str(wallet.locked_balance),
            },
        )
        return wallet

    @staticmethod
    async def finalize_withdrawal(
        db: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        fee: Decimal,
        transaction_id: str,
    ) -> Wallet:
        """
        Finalizes withdrawal upon external partner confirmation.
        Deducts the held funds permanently from balance and releases locked_balance.
        """
        total_debit = amount + fee
        wallet.balance = (wallet.balance - total_debit).quantize(Decimal("0.01"))
        wallet.locked_balance = (wallet.locked_balance - total_debit).quantize(Decimal("0.01"))

        await audit_service.log_action(
            db=db,
            action="WITHDRAWAL_FINALIZED",
            entity_name="Wallet",
            entity_id=wallet.wallet_id,
            actor_id=wallet.user_id,
            new_state={
                "transaction_id": transaction_id,
                "debited": str(total_debit),
                "new_balance": str(wallet.balance),
            },
        )
        return wallet

    @staticmethod
    async def release_hold(
        db: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        fee: Decimal,
        transaction_id: str,
        reason: Optional[str] = None,
    ) -> Wallet:
        """
        Releases held funds when a withdrawal fails or times out.
        Decrements locked_balance back to 0 hold.
        """
        total_hold = amount + fee
        wallet.locked_balance = max(Decimal("0.00"), wallet.locked_balance - total_hold).quantize(Decimal("0.01"))

        await audit_service.log_action(
            db=db,
            action="FUNDS_HOLD_RELEASED",
            entity_name="Wallet",
            entity_id=wallet.wallet_id,
            actor_id=wallet.user_id,
            new_state={
                "transaction_id": transaction_id,
                "released": str(total_hold),
                "reason": reason,
                "locked_balance": str(wallet.locked_balance),
            },
        )
        return wallet

    @staticmethod
    async def credit_deposit(
        db: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        fee: Decimal,
        transaction_id: str,
    ) -> Wallet:
        """
        Credits deposit amount (net of fee) to wallet balance upon partner confirmation.
        """
        net_credit = (amount - fee).quantize(Decimal("0.01"))
        wallet.balance = (wallet.balance + net_credit).quantize(Decimal("0.01"))

        await audit_service.log_action(
            db=db,
            action="DEPOSIT_CREDITED",
            entity_name="Wallet",
            entity_id=wallet.wallet_id,
            actor_id=wallet.user_id,
            new_state={
                "transaction_id": transaction_id,
                "gross_amount": str(amount),
                "fee": str(fee),
                "net_credited": str(net_credit),
                "new_balance": str(wallet.balance),
            },
        )
        return wallet

    @staticmethod
    async def update_status(
        db: AsyncSession,
        wallet_id: str,
        status: WalletStatus,
        reason: str,
        admin_id: str,
    ) -> Wallet:
        wallet = await WalletEngine.get_wallet_with_lock(db, wallet_id, require_active=False)
        old_status = wallet.status.value
        wallet.status = status

        await audit_service.log_action(
            db=db,
            action=f"WALLET_STATUS_{status.value}",
            entity_name="Wallet",
            entity_id=wallet_id,
            actor_id=admin_id,
            old_state={"status": old_status},
            new_state={"status": status.value, "reason": reason},
        )
        await db.commit()
        await db.refresh(wallet)
        return wallet

    @staticmethod
    async def update_limits(
        db: AsyncSession,
        wallet_id: str,
        daily_limit: Decimal,
        monthly_limit: Decimal,
        admin_id: str,
    ) -> Wallet:
        wallet = await WalletEngine.get_wallet_with_lock(db, wallet_id, require_active=False)
        old_state = {
            "daily_limit": str(wallet.daily_limit),
            "monthly_limit": str(wallet.monthly_limit),
        }
        wallet.daily_limit = daily_limit
        wallet.monthly_limit = monthly_limit

        await audit_service.log_action(
            db=db,
            action="WALLET_LIMITS_UPDATED",
            entity_name="Wallet",
            entity_id=wallet_id,
            actor_id=admin_id,
            old_state=old_state,
            new_state={"daily_limit": str(daily_limit), "monthly_limit": str(monthly_limit)},
        )
        await db.commit()
        await db.refresh(wallet)
        return wallet


wallet_engine = WalletEngine()
