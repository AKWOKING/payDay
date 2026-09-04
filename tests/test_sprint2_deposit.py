from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_initiate_deposit_and_webhook_flow(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    # 1. Check initial balance
    bal_res1 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    initial_balance = Decimal(str(bal_res1.json()["data"]["balance"]))

    # 2. Initiate deposit of 10,000 XAF via MTN
    deposit_payload = {
        "channel": "MTN",
        "amount": 10000.00,
        "phone_number": "677112233",
        "idempotency_key": "deposit-key-001",
    }
    dep_res = await client.post("/api/v1/wallet/deposit", json=deposit_payload, headers=user_auth_headers)
    assert dep_res.status_code == 202
    tx_data = dep_res.json()["data"]
    tx_id = tx_data["transaction_id"]
    assert tx_data["channel"] == "MTN"
    assert tx_data["type"] == "DEPOSIT"
    assert Decimal(str(tx_data["amount"])) == Decimal("10000.00")
    assert Decimal(str(tx_data["fee"])) == Decimal("50.00") # 0.5% of 10,000 = 50 XAF
    assert Decimal(str(tx_data["net_amount"])) == Decimal("9950.00")
    external_ref = tx_data["external_ref"]

    # 3. Simulate MTN MoMo Webhook confirmation (Customer enters PIN on phone)
    webhook_payload = {
        "transaction_id": tx_id,
        "external_ref": external_ref,
        "status": "SUCCESSFUL",
        "amount": 10000.00,
    }
    wh_res = await client.post("/api/v1/webhooks/mtn", json=webhook_payload)
    assert wh_res.status_code == 200
    assert wh_res.json()["data"]["status"] == "SUCCESS"

    # 4. Verify wallet balance credited with net amount (9,950 XAF)
    bal_res2 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    new_balance = Decimal(str(bal_res2.json()["data"]["balance"]))
    assert new_balance == initial_balance + Decimal("9950.00")


@pytest.mark.asyncio
async def test_deposit_idempotency(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    deposit_payload = {
        "channel": "MTN",
        "amount": 5000.00,
        "phone_number": "+237699001122",
        "idempotency_key": "unique-idemp-key-123",
    }
    res1 = await client.post("/api/v1/wallet/deposit", json=deposit_payload, headers=user_auth_headers)
    assert res1.status_code == 202
    tx1_id = res1.json()["data"]["transaction_id"]

    # Repeat request with exact same idempotency key
    res2 = await client.post("/api/v1/wallet/deposit", json=deposit_payload, headers=user_auth_headers)
    assert res2.status_code == 202
    tx2_id = res2.json()["data"]["transaction_id"]

    assert tx1_id == tx2_id
