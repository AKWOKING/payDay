"""WS-5 / LB-4 — PIN failure counter moved from a process-local dict to the
shared counter store (Redis in production).

The previous implementation was a class-level dict, so 5 consecutive failures
per process meant up to 5×N attempts with N replicas. These tests pin the
shared-budget property, the reset-on-success behaviour, and the fail-closed
rule when the store is unavailable.
"""

import fakeredis.aioredis
import pytest
from httpx import AsyncClient

from payday.core.counters import RedisCounterStore
from payday.core.ratelimit import pin_failure_key
from payday.models.user import User


@pytest.mark.asyncio
async def test_two_redis_clients_share_pin_failure_budget():
    """Two independent store instances over one Redis enforce a combined count."""
    server = fakeredis.FakeServer()
    store_a = RedisCounterStore(
        fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    )
    store_b = RedisCounterStore(
        fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    )
    key = pin_failure_key("user-shared-budget")

    results = []
    for _ in range(3):
        results.append(await store_a.incr(key, 86400))
    for _ in range(2):
        results.append(await store_b.incr(key, 86400))

    # 3 + 2 = 5 combined, not 5 per process.
    assert [r.count for r in results] == [1, 2, 3, 4, 5]
    assert await store_b.get(key) == 5

    # A correct PIN clears the shared counter.
    await store_b.delete(key)
    assert await store_a.get(key) == 0


@pytest.mark.asyncio
async def test_pin_counter_resets_after_successful_withdrawal(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
):
    """4 failures → correct PIN succeeds (clears counter) → 4 more failures are
    attempts 1..4 again, not cumulative 5..8."""
    set_pin = await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "5566", "password": "SecretP@ssword123"},
        headers=user_auth_headers,
    )
    assert set_pin.status_code == 200

    bad_payload = {
        "channel": "MTN",
        "amount": 5000.00,
        "destination_phone": "+237677112233",
        "pin": "0000",
    }

    for attempt in range(1, 5):
        res = await client.post(
            "/api/v1/wallet/withdraw", json=bad_payload, headers=user_auth_headers
        )
        assert res.status_code == 400
        assert res.json()["code"] == "INVALID_PIN"

    # Correct PIN succeeds (its own counter-clear path is exercised).
    good = await client.post(
        "/api/v1/wallet/withdraw",
        json={
            "channel": "MTN",
            "amount": 5000.00,
            "destination_phone": "+237677112233",
            "pin": "5566",
            "idempotency_key": "ws5-reset-on-success-001",
        },
        headers=user_auth_headers,
    )
    assert good.status_code == 202

    # Counter was cleared: four fresh failures, none of which freeze yet.
    for attempt in range(1, 5):
        res = await client.post(
            "/api/v1/wallet/withdraw", json=bad_payload, headers=user_auth_headers
        )
        assert res.status_code == 400, f"attempt {attempt} should be 400"

    # The 5th after the reset freezes the wallet again.
    frozen = await client.post(
        "/api/v1/wallet/withdraw", json=bad_payload, headers=user_auth_headers
    )
    assert frozen.status_code == 403
    assert frozen.json()["code"] == "WALLET_FROZEN"


class _DownCounterStore:
    """Store whose in-flight operations raise — simulates Redis going away."""

    async def incr(self, key, ttl_seconds):
        raise RuntimeError("Connection refused (simulated Redis outage)")

    async def get(self, key):
        raise RuntimeError("Connection refused (simulated Redis outage)")

    async def delete(self, key):
        raise RuntimeError("Connection refused (simulated Redis outage)")

    async def ping(self):
        return False


@pytest.mark.asyncio
async def test_pin_verify_fails_closed_when_store_unavailable(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
    monkeypatch,
):
    """Redis down in production ⇒ the transaction is refused (503), never
    allowed to proceed unproven."""
    set_pin = await client.post(
        "/api/v1/auth/set-pin",
        json={"pin": "5566", "password": "SecretP@ssword123"},
        headers=user_auth_headers,
    )
    assert set_pin.status_code == 200

    # `services/__init__.py` shadows the submodule attribute with the class, so
    # import the real module object and patch the function it uses.
    from importlib import import_module

    tm_module = import_module("payday.services.transaction_manager")
    monkeypatch.setattr(tm_module, "get_counter_store", lambda: _DownCounterStore())

    res = await client.post(
        "/api/v1/wallet/withdraw",
        json={
            "channel": "MTN",
            "amount": 5000.00,
            "destination_phone": "+237677112233",
            "pin": "0000",
        },
        headers=user_auth_headers,
    )
    assert res.status_code == 503
    body = res.json()
    assert body["code"] == "REDIS_UNAVAILABLE"
    assert "Retry-After" in res.headers
