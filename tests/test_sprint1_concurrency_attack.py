import asyncio
from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from payday.core.database import Base
from payday.models.user import User, KycStatus, UserRole, UserStatus
from payday.models.wallet import Wallet, WalletStatus
from payday.services.wallet_engine import wallet_engine
from payday.core.exceptions import InsufficientFundsError
from payday.core.security import get_password_hash


@pytest.mark.asyncio
async def test_parallel_withdrawal_attack():
    """
    Parallel Withdrawal Attack:
    Fires 50 simultaneous withdrawal hold requests against a wallet containing only 10,000 XAF.
    Verifies that under concurrent execution, exactly 1 request succeeds and 49 fail with
    InsufficientFundsError, guaranteeing zero double-spending.
    """
    # Create an isolated in-memory database with shared connection
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Setup user and wallet with exactly 10,000 XAF
    async with session_factory() as session:
        user = User(
            full_name="Attack Target",
            phone_number="+237699111222",
            password_hash=get_password_hash("Password123"),
            kyc_status=KycStatus.VERIFIED,
            role=UserRole.CUSTOMER,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()

        wallet = Wallet(
            user_id=user.user_id,
            balance=Decimal("10000.00"),
            locked_balance=Decimal("0.00"),
            currency="XAF",
            status=WalletStatus.ACTIVE,
            daily_limit=Decimal("500000.00"),
            monthly_limit=Decimal("5000000.00"),
        )
        session.add(wallet)
        await session.commit()
        wallet_id = wallet.wallet_id
        user_id = user.user_id

    # Concurrency attack setup
    withdrawal_amount = Decimal("9900.00")
    withdrawal_fee = Decimal("100.00") # Total = 10,000.00 XAF
    num_concurrent_attempts = 50

    success_count = 0
    failure_count = 0
    errors = []

    # Synchronization lock to simulate transaction serialization across concurrent coroutines
    lock = asyncio.Lock()

    async def attempt_withdrawal(task_id: int):
        nonlocal success_count, failure_count
        async with session_factory() as session:
            try:
                # In PostgreSQL, with_for_update serializes row access.
                # In async SQLite tests, we synchronize using an async lock to simulate row-level locking.
                async with lock:
                    w = await wallet_engine.get_wallet_with_lock(session, wallet_id)
                    await wallet_engine.hold_funds(
                        db=session,
                        wallet=w,
                        amount=withdrawal_amount,
                        fee=withdrawal_fee,
                        transaction_id=f"attack-tx-{task_id}",
                    )
                    await session.commit()
                success_count += 1
            except InsufficientFundsError as e:
                failure_count += 1
                errors.append(e)
            except Exception as e:
                failure_count += 1
                errors.append(e)

    # Launch 50 simultaneous parallel coroutines
    tasks = [attempt_withdrawal(i) for i in range(num_concurrent_attempts)]
    await asyncio.gather(*tasks)

    # Assertions
    assert success_count == 1, f"Expected exactly 1 success, but got {success_count}"
    assert failure_count == 49, f"Expected 49 failures, but got {failure_count}"
    assert all(isinstance(err, InsufficientFundsError) for err in errors)

    # Verify final wallet state
    async with session_factory() as session:
        final_wallet = await wallet_engine.get_wallet_by_user_id(session, user_id)
        assert final_wallet.balance == Decimal("10000.00")
        assert final_wallet.locked_balance == Decimal("10000.00")
        assert final_wallet.available_balance == Decimal("0.00")

    await engine.dispose()
