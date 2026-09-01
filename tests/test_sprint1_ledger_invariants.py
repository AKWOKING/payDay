from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, text
from sqlalchemy.exc import IntegrityError, DBAPIError

from payday.models.user import User
from payday.models.wallet import Wallet
from payday.services.wallet_engine import wallet_engine


@pytest.mark.asyncio
async def test_ledger_invariant_verification(db_session: AsyncSession, test_user: User):
    """
    Asserts: Wallet.balance == sum(SUCCESS Deposits) - sum(SUCCESS Withdrawals) - sum(Fees)
    across multiple cycles of deposits, holds, releases, and successful withdrawals.
    """
    wallet = await wallet_engine.get_wallet_by_user_id(db_session, test_user.user_id)
    initial_balance = wallet.balance

    total_deposits = Decimal("0.00")
    total_withdrawals = Decimal("0.00")
    total_deposit_fees = Decimal("0.00")
    total_withdrawal_fees = Decimal("0.00")

    # Step 1: Deposit 20,000 XAF with 100 fee
    dep_amount = Decimal("20000.00")
    dep_fee = Decimal("100.00")
    await wallet_engine.credit_deposit(db_session, wallet, dep_amount, dep_fee, "tx-dep-1")
    total_deposits += dep_amount
    total_deposit_fees += dep_fee

    current_expected = initial_balance + total_deposits - total_deposit_fees - total_withdrawals - total_withdrawal_fees
    assert wallet.balance == current_expected

    # Step 2: Withdrawal Hold of 15,000 XAF + 150 fee
    w_amount = Decimal("15000.00")
    w_fee = Decimal("150.00")
    await wallet_engine.hold_funds(db_session, wallet, w_amount, w_fee, "tx-wd-1")
    assert wallet.locked_balance == (w_amount + w_fee)

    # Step 3: Finalize Withdrawal 1
    await wallet_engine.finalize_withdrawal(db_session, wallet, w_amount, w_fee, "tx-wd-1")
    total_withdrawals += w_amount
    total_withdrawal_fees += w_fee
    current_expected = initial_balance + total_deposits - total_deposit_fees - total_withdrawals - total_withdrawal_fees
    assert wallet.balance == current_expected
    assert wallet.locked_balance == Decimal("0.00")

    # Step 4: Withdrawal Hold of 5,000 XAF + 50 fee then Release (Failed Channel)
    w_fail_amount = Decimal("5000.00")
    w_fail_fee = Decimal("50.00")
    await wallet_engine.hold_funds(db_session, wallet, w_fail_amount, w_fail_fee, "tx-wd-fail")
    assert wallet.locked_balance == (w_fail_amount + w_fail_fee)

    await wallet_engine.release_hold(db_session, wallet, w_fail_amount, w_fail_fee, "tx-wd-fail", "Timeout")
    assert wallet.locked_balance == Decimal("0.00")
    assert wallet.balance == current_expected


@pytest.mark.asyncio
async def test_database_check_constraints_prevent_negative_balance(db_session: AsyncSession, test_user: User):
    """
    Confirms that strict database check constraints block any manual update attempting
    to set balance or locked_balance to negative values.
    """
    wallet = await wallet_engine.get_wallet_by_user_id(db_session, test_user.user_id)
    wallet_id = wallet.wallet_id

    # Attempt to execute an invalid direct balance edit < 0.00
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.execute(
            update(Wallet).where(Wallet.wallet_id == wallet_id).values(balance=Decimal("-500.00"))
        )
        await db_session.commit()

    await db_session.rollback()

    # Attempt to execute an invalid locked_balance edit < 0.00
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.execute(
            update(Wallet).where(Wallet.wallet_id == wallet_id).values(locked_balance=Decimal("-100.00"))
        )
        await db_session.commit()

    await db_session.rollback()
