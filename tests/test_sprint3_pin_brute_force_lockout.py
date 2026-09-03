import pytest
from httpx import AsyncClient
from payday.models.user import User


@pytest.mark.asyncio
async def test_pin_brute_force_5x_auto_lockout(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    user_auth_headers: dict,
    admin_auth_headers: dict,
):
    """
    Transaction PIN Failure Threshold & Wallet Lockout:
    1. Set valid PIN = "5566".
    2. Perform 4 consecutive failed withdrawal requests with incorrect PIN ("0000").
       -> Returns HTTP 400 INVALID_PIN (Attempts 1..4).
    3. Perform 5th failed withdrawal request with incorrect PIN.
       -> Triggers automatic wallet suspension (status -> FROZEN).
    4. Subsequent withdrawal attempt with CORRECT PIN ("5566") is rejected immediately with HTTP 403 WALLET_FROZEN.
    5. Verifies audit_logs contains WALLET_AUTO_FROZEN_PIN_BRUTE_FORCE entry.
    6. Verifies security alert notification was dispatched to user.
    """
    # 1. Set PIN
    await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "5566", "password": "SecretP@ssword123"},
        headers=user_auth_headers,
    )

    bad_payload = {
        "channel": "MTN",
        "amount": 5000.00,
        "destination_phone": "+237677112233",
        "pin": "0000", # Wrong PIN
    }

    # 2. First 4 failed attempts -> 400 Bad Request
    for attempt in range(1, 5):
        res = await client.post("/api/v1/wallet/withdraw", json=bad_payload, headers=user_auth_headers)
        assert res.status_code == 400
        assert res.json()["code"] == "INVALID_PIN"

    # 3. 5th failed attempt -> Triggers wallet lockout
    res5 = await client.post("/api/v1/wallet/withdraw", json=bad_payload, headers=user_auth_headers)
    assert res5.status_code == 403
    assert res5.json()["code"] == "WALLET_FROZEN"

    # 4. Attempt with CORRECT PIN -> Still rejected because wallet is FROZEN
    good_payload = {
        "channel": "MTN",
        "amount": 5000.00,
        "destination_phone": "+237677112233",
        "pin": "5566", # Correct PIN
    }
    blocked_res = await client.post("/api/v1/wallet/withdraw", json=good_payload, headers=user_auth_headers)
    assert blocked_res.status_code == 403
    assert blocked_res.json()["code"] == "WALLET_FROZEN"

    # 5. Verify Audit Log entry
    audit_res = await client.get("/api/v1/admin/audit-logs?action=WALLET_AUTO_FROZEN_PIN_BRUTE_FORCE", headers=admin_auth_headers)
    assert audit_res.status_code == 200
    logs = audit_res.json()["data"]["items"]
    assert len(logs) >= 1
    assert logs[0]["action"] == "WALLET_AUTO_FROZEN_PIN_BRUTE_FORCE"

    # 6. Verify Security Notification in inbox
    notif_res = await client.get("/api/v1/notifications", headers=user_auth_headers)
    assert notif_res.status_code == 200
    items = notif_res.json()["data"]["items"]
    assert any("suspended due to 5 consecutive failed PIN attempts" in n["message"] for n in items)
