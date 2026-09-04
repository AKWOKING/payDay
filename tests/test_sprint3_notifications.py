from decimal import Decimal
import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_notification_dispatch_on_deposit_and_query(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """
    Notification Engine Verification:
    1. Triggers deposit and confirms SMS and Push notifications are generated.
    2. Queries GET /api/v1/notifications to verify alerts in customer inbox.
    """
    # 1. Initiate deposit
    dep_res = await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "MTN", "amount": 10000.00, "phone_number": "+237677112233"},
        headers=user_auth_headers,
    )
    assert dep_res.status_code == 202

    # 2. Query user notifications
    notif_res = await client.get("/api/v1/notifications", headers=user_auth_headers)
    assert notif_res.status_code == 200
    data = notif_res.json()["data"]
    assert data["total"] >= 2 # 1 SMS + 1 Push

    items = data["items"]
    channels = [n["channel"] for n in items]
    assert "SMS" in channels
    assert "PUSH" in channels
    assert any("PayDay" in n["message"] for n in items)


@pytest.mark.asyncio
async def test_notification_channel_filtering(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """Verifies filtering notifications by SMS vs PUSH."""
    # Initiate a deposit to generate alerts
    await client.post(
        "/api/v1/wallet/deposit",
        json={"channel": "ORANGE", "amount": 8000.00, "phone_number": "+237699112233"},
        headers=user_auth_headers,
    )

    # Filter SMS only
    sms_res = await client.get("/api/v1/notifications?channel=SMS", headers=user_auth_headers)
    assert sms_res.status_code == 200
    sms_items = sms_res.json()["data"]["items"]
    assert all(n["channel"] == "SMS" for n in sms_items)

    # Filter PUSH only
    push_res = await client.get("/api/v1/notifications?channel=PUSH", headers=user_auth_headers)
    assert push_res.status_code == 200
    push_items = push_res.json()["data"]["items"]
    assert all(n["channel"] == "PUSH" for n in push_items)
