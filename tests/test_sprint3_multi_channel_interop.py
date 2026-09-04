from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_triangle_model_multi_channel_bridge(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    The Triangle Model Interoperability:
    1. Customer has initial balance of 50,000 XAF.
    2. Customer deposits 40,000 XAF via MTN Mobile Money (Gross: 40k, Fee: 200, Net: 39,800 XAF).
       -> Balance increases to 89,800 XAF.
    3. Customer sets PIN and withdraws 30,000 XAF to Orange Money (Gross: 30k, Fee: 300, Total Debited: 30,300 XAF).
       -> Balance drops to 59,500 XAF.
    4. Proves central wallet bridges MTN MoMo <-> Orange Money value exchange seamlessly.
    """
    # 1. Check initial balance: 50,000 XAF
    b1 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(b1.json()["data"]["balance"])) == Decimal("50000.00")

    # 2. MTN Deposit (Cash-In)
    mtn_dep = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 40000.00, "phone_number": "+237677001122"},
        headers=user_auth_headers,
    )
    assert mtn_dep.status_code == 202
    mtn_tx_id = mtn_dep.json()["data"]["transaction_id"]
    mtn_ref = mtn_dep.json()["data"]["external_ref"]

    # Finalize MTN Deposit via Webhook
    await client.post(
        "/api/v1/webhooks/mtn",
        json={"transaction_id": mtn_tx_id, "external_ref": mtn_ref, "status": "SUCCESSFUL"},
    )

    b2 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(b2.json()["data"]["balance"])) == Decimal("89800.00")

    # 3. Orange Money Withdrawal (Cash-Out)
    await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "9900", "password": "SecretP@ssword123"},
        headers=user_auth_headers,
    )

    om_with = await client.post(
        "/api/v1/wallet/withdraw",
        json={
            "channel": "ORANGE",
            "amount": 30000.00,
            "destination_phone": "+237699887766",
            "pin": "9900",
        },
        headers=user_auth_headers,
    )
    assert om_with.status_code == 202
    om_tx_id = om_with.json()["data"]["transaction_id"]
    om_ref = om_with.json()["data"]["external_ref"]

    # Finalize Orange Money Payout via Webhook
    await client.post(
        "/api/v1/webhooks/orange",
        json={"transaction_id": om_tx_id, "external_ref": om_ref, "status": "SUCCESSFUL"},
    )

    # 4. Final Balance Verification
    b3 = await client.get("/api/v1/wallet/balance", headers=user_auth_headers)
    assert Decimal(str(b3.json()["data"]["balance"])) == Decimal("59500.00")
    assert Decimal(str(b3.json()["data"]["locked_balance"])) == Decimal("0.00")
