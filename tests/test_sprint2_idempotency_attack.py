import asyncio
from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_concurrent_and_sequential_idempotency(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Idempotency Verification:
    Fires 10 concurrent requests and 5 sequential requests with the same Idempotency-Key.
    Verifies that the backend processes the transaction ONCE and returns the identical transaction ID.
    """
    shared_key = "idemp-attack-key-999"
    payload = {
        "channel": "MTN",
        "amount": 5000.00,
        "phone_number": "677112233",
        "idempotency_key": shared_key,
    }

    # 1. Fire 10 concurrent requests
    async def make_call():
        return await client.post("/api/v1/wallet/deposit", json=payload, headers=user_auth_headers)

    responses = await asyncio.gather(*[make_call() for _ in range(10)])

    # All responses must be HTTP 202
    assert all(r.status_code == 202 for r in responses)

    # All returned transaction IDs must be identical
    tx_ids = [r.json()["data"]["transaction_id"] for r in responses]
    assert len(set(tx_ids)) == 1, f"Expected exactly 1 unique transaction, got {set(tx_ids)}"
    unique_tx_id = tx_ids[0]

    # 2. Fire 5 sequential requests with same idempotency key
    for _ in range(5):
        seq_res = await client.post("/api/v1/wallet/deposit", json=payload, headers=user_auth_headers)
        assert seq_res.status_code == 202
        assert seq_res.json()["data"]["transaction_id"] == unique_tx_id

    # 3. Verify that only 1 transaction record exists in history
    history_res = await client.get("/api/v1/wallet/transactions", headers=user_auth_headers)
    assert history_res.status_code == 200
    items = history_res.json()["data"]["items"]
    matching_txs = [tx for tx in items if tx["idempotency_key"] == shared_key]
    assert len(matching_txs) == 1
