from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_webhook_signature_validation_rejection(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Signature Validation:
    Sends mock MTN webhook with invalid/forged signature; confirms HTTP 403 rejection.
    """
    # 1. Create a pending deposit
    dep_res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 10000.00, "phone_number": "677112233"},
        headers=user_auth_headers,
    )
    tx_id = dep_res.json()["data"]["transaction_id"]
    external_ref = dep_res.json()["data"]["external_ref"]

    # 2. Post webhook with forged / invalid signature
    fake_headers = {"X-Signature": "invalid-signature"}
    wh_payload = {
        "transaction_id": tx_id,
        "external_ref": external_ref,
        "status": "SUCCESSFUL",
    }
    rejected_res = await client.post("/api/v1/webhooks/mtn", json=wh_payload, headers=fake_headers)
    assert rejected_res.status_code == 403
    assert rejected_res.json()["code"] == "WEBHOOK_UNAUTHORIZED"


@pytest.mark.asyncio
async def test_webhook_replay_attack_prevention(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Replay Attack Prevention:
    Intercepts a valid webhook payload and posts it 5 times consecutively.
    Verifies that only the first triggers a ledger credit, preventing artificial inflation.
    """
    # Initial balance
    bal_res1 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    initial_balance = Decimal(str(bal_res1.json()["data"]["balance"]))

    # Deposit 20,000 XAF (Net = 19,900 XAF)
    dep_res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 20000.00, "phone_number": "677112233"},
        headers=user_auth_headers,
    )
    tx_id = dep_res.json()["data"]["transaction_id"]
    external_ref = dep_res.json()["data"]["external_ref"]

    valid_headers = {"X-Signature": "valid-signature"}
    wh_payload = {
        "transaction_id": tx_id,
        "external_ref": external_ref,
        "status": "SUCCESSFUL",
    }

    # Post webhook 5 times consecutively (simulating replay attack)
    for i in range(5):
        res = await client.post("/api/v1/webhooks/mtn", json=wh_payload, headers=valid_headers)
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "SUCCESS"

    # Verify wallet was credited ONLY ONCE (not 5 times!)
    bal_res2 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    final_balance = Decimal(str(bal_res2.json()["data"]["balance"]))
    expected_balance = initial_balance + Decimal("19900.00")
    assert final_balance == expected_balance, f"Replay attack failed! Balance is {final_balance}, expected {expected_balance}"
