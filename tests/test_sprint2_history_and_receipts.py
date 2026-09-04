import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_transaction_history_and_receipt(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    # Set PIN
    await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "9999", "password": "SecretP@ssword123"},
        headers=user_auth_headers,
    )

    # Deposit
    dep_res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 25000.00, "phone_number": "677112233"},
        headers=user_auth_headers,
    )
    dep_tx_id = dep_res.json()["data"]["transaction_id"]
    dep_ext_ref = dep_res.json()["data"]["external_ref"]

    # Confirm deposit
    await client.post(
        "/api/v1/webhooks/mtn",
        json={"transaction_id": dep_tx_id, "external_ref": dep_ext_ref, "status": "SUCCESSFUL"},
    )

    # Withdraw
    w_res = await client.post(
        "/api/v1/wallet/withdraw",
        json={"channel": "MTN", "amount": 10000.00, "destination_phone": "+237677889900", "pin": "9999"},
        headers=user_auth_headers,
    )
    w_tx_id = w_res.json()["data"]["transaction_id"]

    # Query transaction history
    history_res = await client.get("/api/v1/wallet/transactions?page=1&page_size=10", headers=user_auth_headers)
    assert history_res.status_code == 200
    history_data = history_res.json()["data"]
    assert history_data["total"] >= 2
    assert len(history_data["items"]) >= 2

    # Filter by type
    dep_only_res = await client.get("/api/v1/wallet/transactions?tx_type=DEPOSIT", headers=user_auth_headers)
    assert dep_only_res.status_code == 200
    assert all(item["type"] == "DEPOSIT" for item in dep_only_res.json()["data"]["items"])

    # Query detailed receipt
    receipt_res = await client.get(f"/api/v1/wallet/transactions/{dep_tx_id}", headers=user_auth_headers)
    assert receipt_res.status_code == 200
    receipt = receipt_res.json()["data"]
    assert receipt["transaction_id"] == dep_tx_id
    assert receipt["user_name"] == "Jean-Luc Kamdem"
    assert receipt["currency"] == "XAF"
    assert float(receipt["amount"]) == 25000.00
    assert float(receipt["fee"]) == 125.00
    assert receipt["status"] == "SUCCESS"
