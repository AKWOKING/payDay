from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_withdrawal_pin_validation_and_hold_flow(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    # 1. Set PIN first
    set_pin_payload = {"pin": "5678", "password": "SecretP@ssword123"}
    pin_res = await client.post("/api/v1/auth/set-pin", json=set_pin_payload, headers=user_auth_headers)
    assert pin_res.status_code == 200

    # 2. Attempt withdrawal with invalid PIN
    invalid_payload = {
        "channel": "MTN",
        "amount": 10000.00,
        "destination_phone": "677998800",
        "pin": "0000",
    }
    fail_pin_res = await client.post("/api/v1/wallet/withdraw", json=invalid_payload, headers=user_auth_headers)
    assert fail_pin_res.status_code == 400
    assert fail_pin_res.json()["code"] == "INVALID_PIN"

    # 3. Initiate withdrawal with valid PIN (10,000 XAF + 100 fee = 10,100 total hold)
    valid_payload = {
        "channel": "MTN",
        "amount": 10000.00,
        "destination_phone": "+237677998800",
        "pin": "5678",
        "idempotency_key": "withdraw-tx-key-001",
    }
    w_res = await client.post("/api/v1/wallet/withdraw", json=valid_payload, headers=user_auth_headers)
    assert w_res.status_code == 202
    tx_data = w_res.json()["data"]
    tx_id = tx_data["transaction_id"]
    external_ref = tx_data["external_ref"]

    # 4. Check balance: available balance should be reduced, locked_balance should be 10,100
    bal_res1 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    bal_data1 = bal_res1.json()["data"]
    assert Decimal(str(bal_data1["locked_balance"])) == Decimal("10100.00")
    assert Decimal(str(bal_data1["available_balance"])) == Decimal("39900.00")

    # 5. Simulate MTN MoMo Webhook confirmation (Success)
    wh_payload = {
        "transaction_id": tx_id,
        "external_ref": external_ref,
        "status": "SUCCESSFUL",
    }
    wh_res = await client.post("/api/v1/webhooks/mtn", json=wh_payload)
    assert wh_res.status_code == 200

    # 6. Check balance: permanently debited
    bal_res2 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    bal_data2 = bal_res2.json()["data"]
    assert Decimal(str(bal_data2["balance"])) == Decimal("39900.00")
    assert Decimal(str(bal_data2["locked_balance"])) == Decimal("0.00")


@pytest.mark.asyncio
async def test_withdrawal_failure_and_hold_release(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    # Set PIN
    await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "1122", "password": "SecretP@ssword123"},
        headers=user_auth_headers,
    )

    # Initiate withdrawal
    w_payload = {
        "channel": "MTN",
        "amount": 15000.00,
        "destination_phone": "+237677554433",
        "pin": "1122",
        "idempotency_key": "withdraw-fail-test-key",
    }
    w_res = await client.post("/api/v1/wallet/withdraw", json=w_payload, headers=user_auth_headers)
    assert w_res.status_code == 202
    tx_id = w_res.json()["data"]["transaction_id"]
    external_ref = w_res.json()["data"]["external_ref"]

    # Simulate MTN MoMo Webhook rejection / insufficient telco liquidity
    wh_fail_payload = {
        "transaction_id": tx_id,
        "external_ref": external_ref,
        "status": "FAILED",
        "reason": "Subscriber account barred by operator",
    }
    wh_res = await client.post("/api/v1/webhooks/mtn", json=wh_fail_payload)
    assert wh_res.status_code == 200

    # Verify hold was released and available balance restored to 50,000 XAF
    bal_res = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    bal_data = bal_res.json()["data"]
    assert Decimal(str(bal_data["balance"])) == Decimal("50000.00")
    assert Decimal(str(bal_data["locked_balance"])) == Decimal("0.00")
    assert Decimal(str(bal_data["available_balance"])) == Decimal("50000.00")
