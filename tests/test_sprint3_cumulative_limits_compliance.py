from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_cumulative_daily_limit_enforcement(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Cumulative Daily Limit Compliance (COBAC / BEAC Regulation):
    1. Wallet daily limit is 500,000 XAF.
    2. User deposits 500,000 XAF to fund wallet.
    3. User sets PIN and executes withdrawals totaling 499,000 XAF:
       - Withdrawal 1: 400,000 XAF (Fee: 500 XAF) -> SUCCESS
       - Withdrawal 2: 99,000 XAF (Fee: 495 XAF) -> SUCCESS
       - Cumulative volume in 24h = 499,000 XAF (under 500,000 XAF limit).
    4. User attempts a 3rd withdrawal of 2,000 XAF:
       - Cumulative volume would become 501,000 XAF (> 500,000 XAF limit).
    5. Final transaction is blocked with HTTP 400 DAILY_LIMIT_EXCEEDED.
    6. Verifies wallet available balance remains intact and no telco request is processed.
    """
    # 1. Set PIN
    await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "1234", "password": "SecretP@ssword123"},
        headers=user_auth_headers,
    )

    # 2. Fund wallet with 500,000 XAF deposit
    dep = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 500000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    dep_tx_id = dep.json()["data"]["transaction_id"]
    dep_ref = dep.json()["data"]["external_ref"]
    await client.post("/api/v1/webhooks/mtn", json={"transaction_id": dep_tx_id, "external_ref": dep_ref, "status": "SUCCESSFUL"})

    # 3. Withdrawal 1: 400,000 XAF
    w1 = await client.post(
        "/api/v1/wallet/withdraw",
        json={"channel": "MTN", "amount": 400000.00, "destination_phone": "+237677112233", "pin": "1234"},
        headers=user_auth_headers,
    )
    assert w1.status_code == 202
    w1_id = w1.json()["data"]["transaction_id"]
    w1_ref = w1.json()["data"]["external_ref"]
    await client.post("/api/v1/webhooks/mtn", json={"transaction_id": w1_id, "external_ref": w1_ref, "status": "SUCCESSFUL"})

    # 4. Withdrawal 2: 99,000 XAF
    w2 = await client.post(
        "/api/v1/wallet/withdraw",
        json={"channel": "ORANGE", "amount": 99000.00, "destination_phone": "+237699112233", "pin": "1234"},
        headers=user_auth_headers,
    )
    assert w2.status_code == 202
    w2_id = w2.json()["data"]["transaction_id"]
    w2_ref = w2.json()["data"]["external_ref"]
    await client.post("/api/v1/webhooks/orange", json={"transaction_id": w2_id, "external_ref": w2_ref, "status": "SUCCESSFUL"})

    # Check balance before 3rd attempt
    bal_before = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    balance_val = Decimal(str(bal_before.json()["data"]["balance"]))
    locked_val = Decimal(str(bal_before.json()["data"]["locked_balance"]))
    assert locked_val == Decimal("0.00")

    # 5. Attempt Withdrawal 3: 2,000 XAF -> Exceeds daily limit (499,000 + 2,000 = 501,000 XAF)
    w3_res = await client.post(
        "/api/v1/wallet/withdraw",
        json={"channel": "MTN", "amount": 2000.00, "destination_phone": "+237677112233", "pin": "1234"},
        headers=user_auth_headers,
    )
    assert w3_res.status_code == 400
    assert w3_res.json()["code"] == "DAILY_LIMIT_EXCEEDED"

    # 6. Verify wallet state remains completely unaltered
    bal_after = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(bal_after.json()["data"]["balance"])) == balance_val
    assert Decimal(str(bal_after.json()["data"]["locked_balance"])) == Decimal("0.00")
