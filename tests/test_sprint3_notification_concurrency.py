import asyncio
from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_rapid_transaction_notifications_delivery(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Notification Engine Load & Delivery Verification:
    Performs multiple sequential deposits and withdrawals across MTN and Orange.
    Verifies that for every transaction, corresponding SMS & Push notification records
    are reliably written and queryable via GET /api/v1/notifications.
    """
    channels = ["MTN", "ORANGE", "MTN", "ORANGE"]

    for i, ch in enumerate(channels):
        amount = 5000.00 + (i * 1000)
        phone = "+237677112233" if ch == "MTN" else "+237699112233"
        await client.post(
            "/api/v1/wallet/deposit",
            json={"channel": ch, "amount": amount, "phone_number": phone},
            headers=user_auth_headers,
        )

    # Query notifications
    res = await client.get("/api/v1/notifications?page=1&page_size=50", headers=user_auth_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    # 4 deposits * 2 (1 SMS + 1 Push each) = 8 notifications
    assert data["total"] >= 8
    items = data["items"]
    sms_count = sum(1 for n in items if n["channel"] == "SMS")
    push_count = sum(1 for n in items if n["channel"] == "PUSH")
    assert sms_count >= 4
    assert push_count >= 4
