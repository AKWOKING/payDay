import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from payday.models.user import User
from payday.services.wallet_engine import wallet_engine


@pytest.mark.asyncio
async def test_admin_list_users_and_update_status(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    admin_auth_headers: dict,
    user_auth_headers: dict,
):
    # Admin lists users
    res = await client.get("/api/v1/admin/users", headers=admin_auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["total"] >= 2
    assert len(data["items"]) >= 2

    # Regular customer cannot access admin user list
    forbidden_res = await client.get("/api/v1/admin/users", headers=user_auth_headers)
    assert forbidden_res.status_code == 403

    # Admin suspends customer account
    suspend_res = await client.post(
        f"/api/v1/admin/users/{test_user.user_id}/status?status_val=SUSPENDED&reason=Investigation",
        headers=admin_auth_headers,
    )
    assert suspend_res.status_code == 200
    assert suspend_res.json()["data"]["new_status"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_admin_update_wallet_limits_and_freeze(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    admin_auth_headers: dict,
):
    # Fetch user's wallet via wallet_engine
    wallet = await wallet_engine.get_wallet_by_user_id(db_session, test_user.user_id)
    wallet_id = wallet.wallet_id

    # Freeze wallet
    freeze_res = await client.post(
        f"/api/v1/admin/wallets/{wallet_id}/status",
        json={"status": "FROZEN", "reason": "High-risk alert"},
        headers=admin_auth_headers,
    )
    assert freeze_res.status_code == 200
    assert freeze_res.json()["data"]["status"] == "FROZEN"

    # Update limits
    limits_res = await client.put(
        f"/api/v1/admin/wallets/{wallet_id}/limits",
        json={"daily_limit": 1000000.00, "monthly_limit": 10000000.00},
        headers=admin_auth_headers,
    )
    assert limits_res.status_code == 200
    assert float(limits_res.json()["data"]["daily_limit"]) == 1000000.00
