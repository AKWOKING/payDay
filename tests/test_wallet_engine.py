from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from payday.models.user import User
from payday.models.wallet import WalletStatus
from payday.models.transaction import TransactionType
from payday.services.wallet_engine import wallet_engine
from payday.core.exceptions import InsufficientFundsError, DailyLimitExceededError


@pytest.mark.asyncio
async def test_wallet_balance_and_me(client: AsyncClient, test_user: User, user_auth_headers: dict):
    res = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert Decimal(str(data["balance"])) == Decimal("50000.00")
    assert Decimal(str(data["available_balance"])) == Decimal("50000.00")
    assert data["currency"] == "XAF"
    assert data["status"] == "ACTIVE"

    me_res = await client.get("/api/v1/wallet/me", headers=user_auth_headers)
    assert me_res.status_code == 200
    me_data = me_res.json()["data"]
    assert Decimal(str(me_data["daily_limit"])) == Decimal("500000.00")


@pytest.mark.asyncio
async def test_link_external_account(client: AsyncClient, test_user: User, user_auth_headers: dict):
    # Link MTN account
    link_res = await client.post(
        "/api/v1/wallet/linked-accounts",
        json={
            "provider": "MTN",
            "account_identifier": "677112233",
            "is_default": True,
        },
        headers=user_auth_headers,
    )
    assert link_res.status_code == 201
    assert link_res.json()["data"]["provider"] == "MTN"
    assert link_res.json()["data"]["account_identifier"] == "+237677112233"

    # List linked accounts
    list_res = await client.get("/api/v1/wallet/linked-accounts", headers=user_auth_headers)
    assert list_res.status_code == 200
    accounts = list_res.json()["data"]
    assert len(accounts) == 1
    assert accounts[0]["provider"] == "MTN"


@pytest.mark.asyncio
async def test_wallet_engine_hold_and_finalize(db_session: AsyncSession, test_user: User):
    wallet = await wallet_engine.get_wallet_by_user_id(db_session, test_user.user_id)
    initial_balance = wallet.balance

    # Hold 10,000 + 100 fee
    amount = Decimal("10000.00")
    fee = Decimal("100.00")
    await wallet_engine.hold_funds(db_session, wallet, amount, fee, transaction_id="tx-test-01")
    assert wallet.locked_balance == Decimal("10100.00")
    assert wallet.available_balance == Decimal("39900.00")

    # Finalize withdrawal
    await wallet_engine.finalize_withdrawal(db_session, wallet, amount, fee, transaction_id="tx-test-01")
    assert wallet.balance == Decimal("39900.00")
    assert wallet.locked_balance == Decimal("0.00")
    assert wallet.available_balance == Decimal("39900.00")


@pytest.mark.asyncio
async def test_wallet_engine_hold_and_release(db_session: AsyncSession, test_user: User):
    wallet = await wallet_engine.get_wallet_by_user_id(db_session, test_user.user_id)
    amount = Decimal("5000.00")
    fee = Decimal("50.00")

    # Hold funds
    await wallet_engine.hold_funds(db_session, wallet, amount, fee, transaction_id="tx-test-02")
    assert wallet.locked_balance == Decimal("5050.00")

    # Release hold due to simulated partner failure
    await wallet_engine.release_hold(db_session, wallet, amount, fee, transaction_id="tx-test-02", reason="Telco timeout")
    assert wallet.locked_balance == Decimal("0.00")
    assert wallet.balance == Decimal("50000.00")


@pytest.mark.asyncio
async def test_wallet_engine_insufficient_funds(db_session: AsyncSession, test_user: User):
    wallet = await wallet_engine.get_wallet_by_user_id(db_session, test_user.user_id)
    amount = Decimal("100000.00") # Exceeds 50,000 balance
    fee = Decimal("1000.00")

    with pytest.raises(InsufficientFundsError):
        await wallet_engine.hold_funds(db_session, wallet, amount, fee, transaction_id="tx-test-03")
