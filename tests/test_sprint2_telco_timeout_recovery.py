from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_telco_timeout_compensatory_release(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Telco Timeout & Network Drops:
    Simulates an MTN network timeout or partner crash during disbursement.
    Verifies that funds held in `locked_balance` are released back to available balance
    via compensatory ledger logic, with failure_reason logged.
    """
    # 1. Set PIN
    await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "4321", "password": "SecretP@ssword123"},
        headers=user_auth_headers,
    )

    # 2. Check initial balance: 50,000 XAF
    bal_res1 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(bal_res1.json()["data"]["balance"])) == Decimal("50000.00")
    assert Decimal(str(bal_res1.json()["data"]["locked_balance"])) == Decimal("0.00")

    # 3. Initiate withdrawal of 20,000 XAF + 200 fee = 20,200 hold
    w_res = await client.post(
        "/api/v1/wallet/withdraw",
        json={
            "channel": "MTN",
            "amount": 20000.00,
            "destination_phone": "+237677889900",
            "pin": "4321",
            "idempotency_key": "timeout-test-tx-001",
        },
        headers=user_auth_headers,
    )
    assert w_res.status_code == 202
    tx_data = w_res.json()["data"]
    tx_id = tx_data["transaction_id"]
    external_ref = tx_data["external_ref"]

    # 4. Verify funds are held (Available drops to 29,800 XAF, Locked rises to 20,200 XAF)
    bal_res2 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(bal_res2.json()["data"]["locked_balance"])) == Decimal("20200.00")
    assert Decimal(str(bal_res2.json()["data"]["available_balance"])) == Decimal("29800.00")

    # 5. Simulate Telco Network Timeout / Failure Webhook Callback
    timeout_payload = {
        "transaction_id": tx_id,
        "external_ref": external_ref,
        "status": "FAILED",
        "reason": "MTN Gateway Timeout (HTTP 504 Gateway Timeout)",
    }
    wh_res = await client.post("/api/v1/webhooks/mtn", json=timeout_payload)
    assert wh_res.status_code == 200
    assert wh_res.json()["data"]["status"] == "FAILED"

    # 6. Verify Compensatory Release (Available balance restored to 50,000 XAF, Locked returned to 0)
    bal_res3 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(bal_res3.json()["data"]["balance"])) == Decimal("50000.00")
    assert Decimal(str(bal_res3.json()["data"]["locked_balance"])) == Decimal("0.00")
    assert Decimal(str(bal_res3.json()["data"]["available_balance"])) == Decimal("50000.00")

    # 7. Check Receipt shows Failure Reason
    receipt_res = await client.get(f"/api/v1/wallet/transactions/{tx_id}", headers=user_auth_headers)
    assert receipt_res.status_code == 200
    receipt = receipt_res.json()["data"]
    assert receipt["status"] == "FAILED"
    assert "Timeout" in receipt["message"]
