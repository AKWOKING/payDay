from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_orange_money_deposit_and_webhook_flow(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Orange Money Cash-In Flow:
    1. Initiate deposit via Orange Money.
    2. Receive 202 Accepted with order/pay token and PROCESSING status.
    3. Simulate Orange Money Webhook / IPN callback with SUCCESSFUL status.
    4. Confirm wallet is credited with net amount (gross minus 0.5% fee).
    """
    bal_res1 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    initial_balance = Decimal(str(bal_res1.json()["data"]["balance"]))

    # 1. Initiate Orange Money Deposit: 20,000 XAF (Fee: 100 XAF, Net: 19,900 XAF)
    payload = {
        "channel": "ORANGE",
        "amount": 20000.00,
        "phone_number": "+237699445566",
        "idempotency_key": "om-deposit-key-001",
    }
    dep_res = await client.post("/api/v1/wallet/deposit", json=payload, headers=user_auth_headers)
    assert dep_res.status_code == 202
    tx_data = dep_res.json()["data"]
    tx_id = tx_data["transaction_id"]
    external_ref = tx_data["external_ref"]
    assert tx_data["channel"] == "ORANGE"
    assert tx_data["status"] == "PROCESSING"
    assert Decimal(str(tx_data["amount"])) == Decimal("20000.00")
    assert Decimal(str(tx_data["fee"])) == Decimal("100.00")
    assert Decimal(str(tx_data["net_amount"])) == Decimal("19900.00")

    # 2. Orange Money Webhook / IPN Confirmation
    wh_payload = {
        "transaction_id": tx_id,
        "external_ref": external_ref,
        "status": "SUCCESSFUL",
        "amount": 20000.00,
    }
    wh_res = await client.post("/api/v1/webhooks/orange", json=wh_payload)
    assert wh_res.status_code == 200
    assert wh_res.json()["data"]["status"] == "SUCCESS"

    # 3. Verify Wallet Credit
    bal_res2 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    final_balance = Decimal(str(bal_res2.json()["data"]["balance"]))
    assert final_balance == initial_balance + Decimal("19900.00")


@pytest.mark.asyncio
async def test_orange_money_withdrawal_and_payout_flow(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Orange Money Cash-Out Flow:
    1. Set Transaction PIN.
    2. Initiate withdrawal of 10,000 XAF (Fee: 100 XAF, Total Hold: 10,100 XAF).
    3. Verify hold on locked_balance.
    4. Simulate Orange Money Payout Webhook Confirmation.
    5. Verify final balance debit and locked_balance clearance.
    """
    # 1. Set PIN
    await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "7788", "password": "SecretP@ssword123"},
        headers=user_auth_headers,
    )

    bal_res1 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    initial_balance = Decimal(str(bal_res1.json()["data"]["balance"]))

    # 2. Initiate Orange Money Withdrawal
    w_payload = {
        "channel": "ORANGE",
        "amount": 10000.00,
        "destination_phone": "+237699778899",
        "pin": "7788",
        "idempotency_key": "om-withdraw-key-001",
    }
    w_res = await client.post("/api/v1/wallet/withdraw", json=w_payload, headers=user_auth_headers)
    assert w_res.status_code == 202
    tx_data = w_res.json()["data"]
    tx_id = tx_data["transaction_id"]
    external_ref = tx_data["external_ref"]

    # 3. Verify funds hold
    bal_res2 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(bal_res2.json()["data"]["locked_balance"])) == Decimal("10100.00")
    assert Decimal(str(bal_res2.json()["data"]["available_balance"])) == initial_balance - Decimal("10100.00")

    # 4. Simulate Orange Money Webhook Payout Success
    wh_payload = {
        "transaction_id": tx_id,
        "external_ref": external_ref,
        "status": "SUCCESSFUL",
    }
    wh_res = await client.post("/api/v1/webhooks/orange", json=wh_payload)
    assert wh_res.status_code == 200
    assert wh_res.json()["data"]["status"] == "SUCCESS"

    # 5. Verify finalized ledger debit
    bal_res3 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(bal_res3.json()["data"]["balance"])) == initial_balance - Decimal("10100.00")
    assert Decimal(str(bal_res3.json()["data"]["locked_balance"])) == Decimal("0.00")


@pytest.mark.asyncio
async def test_orange_money_webhook_signature_rejection(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Orange Money Webhook HMAC Signature Security:
    Rejects tampered or forged webhook signatures with HTTP 403.
    """
    dep_res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "ORANGE", "amount": 5000.00, "phone_number": "+237699112233"},
        headers=user_auth_headers,
    )
    tx_id = dep_res.json()["data"]["transaction_id"]
    external_ref = dep_res.json()["data"]["external_ref"]

    fake_headers = {"X-Orange-Signature": "invalid-forged-signature"}
    wh_payload = {
        "transaction_id": tx_id,
        "external_ref": external_ref,
        "status": "SUCCESSFUL",
    }
    rejected_res = await client.post("/api/v1/webhooks/orange", json=wh_payload, headers=fake_headers)
    assert rejected_res.status_code == 403
    assert rejected_res.json()["code"] == "WEBHOOK_UNAUTHORIZED"
