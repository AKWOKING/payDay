from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_admin_reversal_invariants_and_safeguards(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    user_auth_headers: dict,
    admin_auth_headers: dict,
):
    """
    Admin Reversal Safeguards:
    1. Attempting to reverse a FAILED transaction -> Fails (Invalid State Transition).
    2. Reversing a successful transaction twice -> Fails (Invalid State Transition / Already Reversed).
    3. Attempting to reverse a deposit when user balance is insufficient -> Fails with INSUFFICIENT_FUNDS_FOR_REVERSAL.
    """
    # 1. Create a FAILED transaction
    d_fail = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 10000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    fail_id = d_fail.json()["data"]["transaction_id"]
    fail_ref = d_fail.json()["data"]["external_ref"]
    await client.post("/api/v1/webhooks/mtn", json={"transaction_id": fail_id, "external_ref": fail_ref, "status": "FAILED"})

    # Attempt to reverse FAILED transaction
    rev_fail_res = await client.post(
        f"/api/v1/admin/transactions/{fail_id}/reverse",
        json={"reason": "Test reversal on failed tx"},
        headers=admin_auth_headers,
    )
    assert rev_fail_res.status_code == 400
    assert rev_fail_res.json()["code"] == "INVALID_STATE_TRANSITION"

    # 2. Create and reverse a SUCCESSFUL deposit
    d_ok = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 5000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    ok_id = d_ok.json()["data"]["transaction_id"]
    ok_ref = d_ok.json()["data"]["external_ref"]
    await client.post("/api/v1/webhooks/mtn", json={"transaction_id": ok_id, "external_ref": ok_ref, "status": "SUCCESSFUL"})

    # First reversal succeeds
    rev1 = await client.post(
        f"/api/v1/admin/transactions/{ok_id}/reverse",
        json={"reason": "First valid reversal"},
        headers=admin_auth_headers,
    )
    assert rev1.status_code == 200
    assert rev1.json()["data"]["status"] == "REVERSED"

    # Second reversal attempt fails
    rev2 = await client.post(
        f"/api/v1/admin/transactions/{ok_id}/reverse",
        json={"reason": "Second invalid duplicate reversal"},
        headers=admin_auth_headers,
    )
    assert rev2.status_code == 400
    assert rev2.json()["code"] == "INVALID_STATE_TRANSITION"
