from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_automated_reconciliation_balanced(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    user_auth_headers: dict,
    admin_auth_headers: dict,
):
    """
    Automated Financial Reconciliation (Balanced Flow):
    1. Generates successful MTN transactions.
    2. Runs reconciliation without external discrepancies.
    3. Asserts is_balanced == True and mismatches_count == 0.
    """
    # 1. Generate transactions
    d1 = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 10000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    tx1_id = d1.json()["data"]["transaction_id"]
    ref1 = d1.json()["data"]["external_ref"]
    await client.post("/api/v1/webhooks/mtn", json={"transaction_id": tx1_id, "external_ref": ref1, "status": "SUCCESSFUL"})

    # 2. Run Reconciliation
    recon_payload = {
        "channel": "MTN",
        "partner_records": [
            {
                "external_ref": ref1,
                "amount": 10000.00,
                "currency": "XAF",
                "status": "SUCCESS",
                "channel": "MTN",
            }
        ],
    }
    rec_res = await client.post("/api/v1/admin/reconcile", json=recon_payload, headers=admin_auth_headers)
    assert rec_res.status_code == 200
    report = rec_res.json()["data"]
    assert report["channel"] == "MTN"
    assert report["matched_count"] == 1
    assert report["mismatches_count"] == 0
    assert report["is_balanced"] is True


@pytest.mark.asyncio
async def test_reconciliation_mismatch_amount_and_status(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    user_auth_headers: dict,
    admin_auth_headers: dict,
):
    """
    Reconciliation Variance Detection:
    1. Customer creates Orange Money deposit for 15,000 XAF.
    2. Partner settlement file reports amount variance (14,000 XAF instead of 15,000 XAF).
    3. Asserts reconciliation detects AMOUNT_MISMATCH and marks report is_balanced == False.
    """
    d1 = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "ORANGE", "amount": 15000.00, "phone_number": "+237699112233"},
        headers=user_auth_headers,
    )
    tx1_id = d1.json()["data"]["transaction_id"]
    ref1 = d1.json()["data"]["external_ref"]

    recon_payload = {
        "channel": "ORANGE",
        "partner_records": [
            {
                "external_ref": ref1,
                "amount": 14000.00, # Amount mismatch
                "currency": "XAF",
                "status": "SUCCESS",
                "channel": "ORANGE",
            }
        ],
    }
    rec_res = await client.post("/api/v1/admin/reconcile", json=recon_payload, headers=admin_auth_headers)
    assert rec_res.status_code == 200
    report = rec_res.json()["data"]
    assert report["is_balanced"] is False
    assert report["mismatches_count"] >= 1
    mismatch = report["mismatches"][0]
    assert mismatch["mismatch_type"] == "AMOUNT_MISMATCH"
    assert Decimal(str(mismatch["internal_amount"])) == Decimal("15000.00")
    assert Decimal(str(mismatch["partner_amount"])) == Decimal("14000.00")
