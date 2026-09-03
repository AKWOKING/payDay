import asyncio
import os
import sys
from decimal import Decimal
from typing import AsyncGenerator
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure src is in sys.path
sys.path.insert(0, os.path.abspath("src"))

from payday.main import app
from payday.core.database import Base, get_db
from payday.core.security import get_password_hash, create_access_token
from payday.models.user import User, KycStatus, UserRole, UserStatus
from payday.models.wallet import Wallet, WalletStatus

# Use an in-memory SQLite with StaticPool so multiple sessions share the same memory database
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    future=True,
)

TestAsyncSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestAsyncSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        async with TestAsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        full_name="Jean-Luc Kamdem",
        phone_number="+237699112233",
        email="jeanluc@example.cm",
        password_hash=get_password_hash("SecretP@ssword123"),
        kyc_status=KycStatus.VERIFIED,
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.flush()

    wallet = Wallet(
        user_id=user.user_id,
        balance=Decimal("50000.00"),
        locked_balance=Decimal("0.00"),
        currency="XAF",
        status=WalletStatus.ACTIVE,
        daily_limit=Decimal("500000.00"),
        monthly_limit=Decimal("5000000.00"),
    )
    db_session.add(wallet)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_admin(db_session: AsyncSession) -> User:
    admin = User(
        full_name="Compliance Admin",
        phone_number="+237677000111",
        email="admin@payday.cm",
        password_hash=get_password_hash("AdminP@ssword123"),
        kyc_status=KycStatus.VERIFIED,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db_session.add(admin)
    await db_session.flush()

    wallet = Wallet(
        user_id=admin.user_id,
        balance=Decimal("0.00"),
        locked_balance=Decimal("0.00"),
        currency="XAF",
        status=WalletStatus.ACTIVE,
    )
    db_session.add(wallet)
    await db_session.commit()
    await db_session.refresh(admin)
    return admin


@pytest_asyncio.fixture
def user_auth_headers(test_user: User) -> dict:
    token = create_access_token(subject=test_user.user_id, role=test_user.role.value)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
def admin_auth_headers(test_admin: User) -> dict:
    token = create_access_token(subject=test_admin.user_id, role=test_admin.role.value)
    return {"Authorization": f"Bearer {token}"}
