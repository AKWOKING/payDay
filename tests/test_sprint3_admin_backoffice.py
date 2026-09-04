from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_admin_transaction_search_and_filtering(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    user_auth_headers: dict,
    admin_auth_headers: dict,
):
    """
    Admin Transaction Feed:
    1. Customer creates MTN deposit and Orange deposit.
    2. Admin queries GET /api/v1/admin/transactions with filtering.
    """
    # 1. Create transactions
    await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 12000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "ORANGE", "amount": 18000.00, "phone_number": "+237699112233"},
        headers=user_auth_headers,
    )

    # 2. Admin queries all transactions
    admin_res = await client.get("/api/v1/admin/transactions", headers=admin_auth_headers)
    assert admin_res.status_code == 200
    tx_list = admin_res.json()["data"]["items"]
    assert len(tx_list) >= 2

    # 3. Filter by MTN channel
    mtn_res = await client.get("/api/v1/admin/transactions?channel=MTN", headers=admin_auth_headers)
    assert mtn_res.status_code == 200
    assert all(tx["channel"] == "MTN" for tx in mtn_res.json()["data"]["items"])


@pytest.mark.asyncio
async def test_admin_manual_transaction_reversal(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    user_auth_headers: dict,
    admin_auth_headers: dict,
):
    """
    Admin Manual Reversal:
    1. Customer initiates and completes deposit of 10,000 XAF (Net = 9,950 XAF).
    2. Admin reverses transaction with reason.
    3. Verifies transaction status is REVERSED and wallet balance is debited 9,950 XAF.
    """
    bal_res1 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    initial_balance = Decimal(str(bal_res1.json()["data"]["balance"]))

    # 1. Deposit and finalize
    dep_res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 10000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    tx_id = dep_res.json()["data"]["transaction_id"]
    external_ref = dep_res.json()["data"]["external_ref"]

    await client.post(
        "/api/v1/webhooks/mtn",
        json={"transaction_id": tx_id, "external_ref": external_ref, "status": "SUCCESSFUL"},
    )

    # Balance after deposit
    bal_res2 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(bal_res2.json()["data"]["balance"])) == initial_balance + Decimal("9950.00")

    # 2. Admin performs manual reversal
    rev_payload = {
        "reason": "Customer fraudulent dispute reported by operator",
        "admin_notes": "Ticket #DISP-99201 verified by compliance team",
    }
    rev_res = await client.post(
        f"/api/v1/admin/transactions/{tx_id}/reverse",
        json=rev_payload,
        headers=admin_auth_headers,
    )
    assert rev_res.status_code == 200
    rev_data = rev_res.json()["data"]
    assert rev_data["status"] == "REVERSED"

    # 3. Verify wallet balance was reversed back to initial balance
    bal_res3 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(bal_res3.json()["data"]["balance"])) == initial_balance


@pytest.mark.asyncio
async def test_admin_audit_logs_feed(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    admin_auth_headers: dict,
):
    """Verifies querying immutable system audit logs."""
    # Perform an admin action that triggers audit log
    await client.post(
        f"/api/v1/admin/users/{test_user.user_id}/status?status_val=ACTIVE&reason=Routine+audit+verification",
        headers=admin_auth_headers,
    )

    res = await client.get("/api/v1/admin/audit-logs", headers=admin_auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["total"] >= 1
    assert "items" in data
    assert any(log["entity_name"] == "User" for log in data["items"])
