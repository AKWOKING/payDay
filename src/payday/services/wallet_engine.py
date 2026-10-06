from decimal import Decimal
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from payday.core.config import settings
from payday.core.money import whole_xaf
from payday.models.wallet import Wallet, WalletStatus
from payday.models.transaction import TransactionDirection, TransactionType
from payday.core.exceptions import (
    BalanceCeilingExceededError,
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
        """Computes the PayDay platform fee for a transaction.

        The result is a **whole number of francs**: XAF has no minor unit, so a
        percentage fee such as 1% of 12 345 XAF (123.45) is not a representable
        amount — it cannot be charged, refunded or reconciled to an operator
        statement. `core.money.whole_xaf` applies the configured rounding policy
        (D26) and the minimum fee is applied afterwards so the floor stays whole
        too.
        """
        # PayDay -> PayDay is free, and a minimum-fee floor would silently make
        # it cost 25 XAF, so the floor is per-type rather than global.
        if tx_type == TransactionType.DEPOSIT:
            fee_pct = Decimal(str(settings.DEFAULT_DEPOSIT_FEE_PERCENTAGE))
        elif tx_type == TransactionType.TRANSFER:
            fee_pct = Decimal(str(settings.DEFAULT_TRANSFER_FEE_PERCENTAGE))
        else:
            fee_pct = Decimal(str(settings.DEFAULT_WITHDRAW_FEE_PERCENTAGE))
        floors_at_minimum = tx_type != TransactionType.TRANSFER

        calculated_fee = whole_xaf(amount * fee_pct)
        if not floors_at_minimum:
            return calculated_fee
        min_fee = whole_xaf(Decimal(str(settings.MIN_FEE_AMOUNT)))
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
    async def get_cumulative_daily_volume(
        db: AsyncSession,
        wallet_id: str,
        tx_type: TransactionType = TransactionType.WITHDRAW,
    ) -> Decimal:
        """Total of one transaction type over the last 24 hours.

        Kept for callers that ask about a single type; limit enforcement uses
        `get_outgoing_volume`, which sums every way money can leave a wallet.
        """
        from datetime import datetime, timedelta, timezone
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        return await WalletEngine._sum_transactions(
            db, wallet_id, [tx_type], since
        )

    @staticmethod
    async def _sum_transactions(
        db: AsyncSession,
        wallet_id: str,
        tx_types: list,
        since,
    ) -> Decimal:
        """Sum of amounts for the given types since a moment.

        SUCCESS and PROCESSING both count: a pending withdrawal has already
        committed the customer's money, so treating it as "not spent yet" would
        let a customer exceed their limit by queueing transactions.
        """
        from payday.models.transaction import Transaction, TransactionStatus
        from sqlalchemy import func

        query = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.wallet_id == wallet_id,
            Transaction.type.in_(tx_types),
            Transaction.direction == TransactionDirection.DEBIT,
            Transaction.status.in_(
                [TransactionStatus.SUCCESS, TransactionStatus.PROCESSING]
            ),
            Transaction.created_at >= since,
        )
        result = await db.execute(query)
        return Decimal(str(result.scalar_one()))

    @staticmethod
    async def get_outgoing_volume(
        db: AsyncSession,
        wallet_id: str,
        window: str = "day",
    ) -> Decimal:
        """How much this wallet has sent out in the window ("day" or "month").

        Everything that debits counts: withdrawals, internal transfers, and (when
        they exist) bill payments and agent cash-out. Summing only withdrawals —
        which is what the code did — meant a customer could move unlimited money
        out through any other debit path.
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        if window == "day":
            since = now - timedelta(hours=24)
        elif window == "month":
            since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:  # pragma: no cover - programming error
            raise ValueError(f"unknown window {window!r}")

        debit_types = [
            TransactionType.WITHDRAW,
            TransactionType.TRANSFER,
        ]
        return await WalletEngine._sum_transactions(db, wallet_id, debit_types, since)

    @staticmethod
    async def validate_withdrawal_capacity(
        db: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        fee: Decimal,
    ) -> None:
        """Sufficient available balance, and within the daily and monthly ceilings.

        `monthly_limit` existed on the wallet, was settable by an admin and was
        returned to clients, but was **never enforced** (LB-15). It is enforced
        here now, for every outgoing path, because the only correct place to
        check it is the one every path already goes through.
        """
        await WalletEngine.validate_outgoing_capacity(db, wallet, amount, fee)

    @staticmethod
    async def validate_outgoing_capacity(
        db: AsyncSession,
        wallet: Wallet,
        amount: Decimal,
        fee: Decimal,
    ) -> None:
        """Available balance plus daily and monthly outgoing ceilings."""
        total_required = amount + fee
        available = wallet.balance - wallet.locked_balance

        if available < total_required:
            raise InsufficientFundsError(available=float(available), required=float(total_required))

        daily_volume = await WalletEngine.get_outgoing_volume(db, wallet.wallet_id, "day")
        if (daily_volume + amount) > wallet.daily_limit:
            raise DailyLimitExceededError(
                limit=float(wallet.daily_limit),
                current_total=float(daily_volume),
                requested=float(amount)
            )

        monthly_volume = await WalletEngine.get_outgoing_volume(db, wallet.wallet_id, "month")
        if (monthly_volume + amount) > wallet.monthly_limit:
            raise MonthlyLimitExceededError(
                limit=float(wallet.monthly_limit),
                current_total=float(monthly_volume),
                requested=float(amount)
            )

    @staticmethod
    def validate_credit_capacity(wallet: Wallet, amount: Decimal) -> None:
        """Refuse a credit that would push the wallet past its ceiling.

        Enforced *before* the counterparty is debited, so a full wallet cannot
        leave a transfer half-done. The ceiling exists because an e-money wallet
        is a stored-value instrument with a regulatory maximum; the published
        figure and the enforced one must be the same number (D-28).
        """
        ceiling = Decimal(str(settings.MAX_WALLET_BALANCE))
        if ceiling <= 0:
            return
        projected = wallet.balance + amount
        if projected > ceiling:
            raise BalanceCeilingExceededError(
                ceiling=float(ceiling),
                current_balance=float(wallet.balance),
                attempted_credit=float(amount),
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
        await WalletEngine.validate_withdrawal_capacity(db, wallet, amount, fee)

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
        # Checked before the balance moves: no credit path may exceed the ceiling,
        # and refusing here means a refused deposit leaves the wallet untouched.
        WalletEngine.validate_credit_capacity(wallet, net_credit)
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
    async def lock_wallets_in_order(
        db: AsyncSession,
        wallet_ids: list,
    ) -> dict:
        """Lock several wallets in a deterministic order and return them by id.

        Sender and recipient are always locked in ascending `wallet_id` order.
        Without a fixed order, two transfers in opposite directions at the same
        moment can each hold the row the other wants and deadlock — the classic
        way a payments system stops working under load, and the hardest to
        reproduce. Sorting makes that impossible by construction.
        """
        wallets = {}
        for wallet_id in sorted(wallet_ids):
            wallets[wallet_id] = await WalletEngine.get_wallet_with_lock(db, wallet_id)
        return wallets

    @staticmethod
    async def transfer_funds(
        db: AsyncSession,
        sender_wallet: Wallet,
        recipient_wallet: Wallet,
        amount: Decimal,
        group_id: str,
        sender_tx_id: str,
        recipient_tx_id: str,
    ) -> None:
        """Move `amount` between two wallets, atomically, in both directions.

        One debit and one credit in the same unit of work: there is no state in
        which money has left one wallet and not arrived in the other, because the
        two updates commit together or not at all. Both legs are audited, and the
        pair carries a shared `transfer_group_id` so the conservation of the
        group (Σ = 0) can be checked later by anyone, not just by a test.

        Caller responsibilities: acquire both locks first (see
        `lock_wallets_in_order`), validate balance, limits and the recipient's
        ceiling *before* calling this, and own the commit.
        """
        sender_wallet.balance = (sender_wallet.balance - amount).quantize(Decimal("0.01"))
        recipient_wallet.balance = (recipient_wallet.balance + amount).quantize(Decimal("0.01"))

        await audit_service.log_action(
            db=db,
            action="TRANSFER_SENT",
            entity_name="Wallet",
            entity_id=sender_wallet.wallet_id,
            actor_id=sender_wallet.user_id,
            new_state={
                "transfer_group_id": group_id,
                "transaction_id": sender_tx_id,
                "counterparty_transaction_id": recipient_tx_id,
                "debit": str(amount),
                "new_balance": str(sender_wallet.balance),
            },
        )
        await audit_service.log_action(
            db=db,
            action="TRANSFER_RECEIVED",
            entity_name="Wallet",
            entity_id=recipient_wallet.wallet_id,
            actor_id=recipient_wallet.user_id,
            new_state={
                "transfer_group_id": group_id,
                "transaction_id": recipient_tx_id,
                "counterparty_transaction_id": sender_tx_id,
                "credit": str(amount),
                "new_balance": str(recipient_wallet.balance),
            },
        )

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
