from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_reconciliation_comprehensive_matrix(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    user_auth_headers: dict,
    admin_auth_headers: dict,
):
    """
    Reconciliation Comprehensive Edge Case Matrix:
    1. TX1 (Matched): MTN Deposit of 10,000 XAF -> Matches Partner Settlement.
    2. TX2 (Amount Variance): MTN Deposit of 25,000 XAF -> Partner settlement states 20,000 XAF.
    3. TX3 (Status Variance): MTN Deposit of 5,000 XAF -> Internal shows FAILED, Partner shows SUCCESSFUL.
    4. TX4 (Missing in Internal): Partner has settlement ref 'MTN-GHOST-999' not in PayDay DB.
    5. Asserts report identifies all 3 variances and categorizes correctly.
    """
    # 1. TX1 (Matched)
    tx1_res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 10000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    tx1_id = tx1_res.json()["data"]["transaction_id"]
    ref1 = tx1_res.json()["data"]["external_ref"]
    await client.post("/api/v1/webhooks/mtn", json={"transaction_id": tx1_id, "external_ref": ref1, "status": "SUCCESSFUL"})

    # 2. TX2 (Amount Mismatch)
    tx2_res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 25000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    tx2_id = tx2_res.json()["data"]["transaction_id"]
    ref2 = tx2_res.json()["data"]["external_ref"]
    await client.post("/api/v1/webhooks/mtn", json={"transaction_id": tx2_id, "external_ref": ref2, "status": "SUCCESSFUL"})

    # 3. TX3 (Status Mismatch - Failed on PayDay)
    tx3_res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 5000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    tx3_id = tx3_res.json()["data"]["transaction_id"]
    ref3 = tx3_res.json()["data"]["external_ref"]
    await client.post("/api/v1/webhooks/mtn", json={"transaction_id": tx3_id, "external_ref": ref3, "status": "FAILED", "reason": "Declined"})

    # Submit Partner Settlement Feed with deliberate variances
    partner_feed = [
        # Matched
        {"external_ref": ref1, "amount": 10000.00, "currency": "XAF", "status": "SUCCESS", "channel": "MTN"},
        # Amount mismatch (20k vs 25k)
        {"external_ref": ref2, "amount": 20000.00, "currency": "XAF", "status": "SUCCESS", "channel": "MTN"},
        # Status mismatch (Partner says SUCCESS, PayDay recorded FAILED)
        {"external_ref": ref3, "amount": 5000.00, "currency": "XAF", "status": "SUCCESS", "channel": "MTN"},
        # Ghost partner transaction
        {"external_ref": "MTN-GHOST-999", "amount": 15000.00, "currency": "XAF", "status": "SUCCESS", "channel": "MTN"},
    ]

    recon_res = await client.post(
        "/api/v1/admin/reconcile",
        json={"channel": "MTN", "partner_records": partner_feed},
        headers=admin_auth_headers,
    )
    assert recon_res.status_code == 200
    report = recon_res.json()["data"]
    assert report["is_balanced"] is False
    assert report["matched_count"] == 1
    assert report["mismatches_count"] == 3

    mismatch_types = [m["mismatch_type"] for m in report["mismatches"]]
    assert "AMOUNT_MISMATCH" in mismatch_types
    assert "STATUS_MISMATCH" in mismatch_types
    assert "MISSING_IN_INTERNAL" in mismatch_types
