"""WS-0 — shared counter store, rate limiter, and fail-closed startup.

These tests cover the pieces LB-4/LB-6 were missing:

- a limiter that allows N, rejects N+1, and recovers after the window;
- a budget shared across two independent limiter instances (the test that
  would have caught the per-process PIN counter);
- the Redis client path exercised against `fakeredis` (pure-Python, so the
  suite can run without a Redis binary);
- production fail-closed rules from docs/LAUNCH_BLOCKER_ROADMAP.md WS-0.
"""

import os

import fakeredis.aioredis
import pytest

from payday.core.counters import (
    MemoryCounterStore,
    RedisCounterStore,
    check_redis_connection,
    ensure_counters_ready,
    validate_counter_configuration,
)
from payday.core.config import settings
from payday.core.exceptions import RateLimitError
from payday.core.ratelimit import RateLimiter, enforce_rate_limit


class FakeClock:
    """Deterministic monotonic clock for window-expiry tests."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.mark.asyncio
async def test_limiter_allows_n_rejects_n_plus_one():
    store = MemoryCounterStore()
    limiter = RateLimiter(store)

    for expected in (1, 2, 3):
        decision = await limiter.hit("rate:test:key", limit=3, window_seconds=60)
        assert decision.allowed is True
        assert decision.count == expected
        assert decision.remaining == 3 - expected

    blocked = await limiter.hit("rate:test:key", limit=3, window_seconds=60)
    assert blocked.allowed is False
    assert blocked.count == 4
    assert blocked.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_limiter_recovers_after_window():
    clock = FakeClock()
    store = MemoryCounterStore(now_fn=clock)
    limiter = RateLimiter(store)

    for _ in range(3):
        await limiter.hit("rate:test:key", limit=3, window_seconds=60)
    blocked = await limiter.hit("rate:test:key", limit=3, window_seconds=60)
    assert blocked.allowed is False

    clock.advance(61)
    recovered = await limiter.hit("rate:test:key", limit=3, window_seconds=60)
    assert recovered.allowed is True
    assert recovered.count == 1


@pytest.mark.asyncio
async def test_two_limiter_instances_share_one_memory_budget():
    """Two instances over one backend enforce a shared budget (the LB-4 shape)."""
    store = MemoryCounterStore()
    limiter_a = RateLimiter(store)
    limiter_b = RateLimiter(store)

    assert (await limiter_a.hit("rate:test:key", limit=3, window_seconds=60)).count == 1
    assert (await limiter_a.hit("rate:test:key", limit=3, window_seconds=60)).count == 2
    assert (await limiter_b.hit("rate:test:key", limit=3, window_seconds=60)).count == 3
    blocked = await limiter_b.hit("rate:test:key", limit=3, window_seconds=60)
    assert blocked.allowed is False


@pytest.mark.asyncio
async def test_two_redis_clients_share_one_budget():
    """Real Redis code path: two clients over one fakeredis server share state."""
    server = fakeredis.FakeServer()
    store_a = RedisCounterStore(
        fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    )
    store_b = RedisCounterStore(
        fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    )

    assert (await store_a.incr("rate:test:key", 60)).count == 1
    assert (await store_b.incr("rate:test:key", 60)).count == 2
    assert (await store_a.incr("rate:test:key", 60)).count == 3

    assert await store_b.get("rate:test:key") == 3
    await store_a.delete("rate:test:key")
    assert await store_b.get("rate:test:key") == 0


@pytest.mark.asyncio
async def test_enforce_rate_limit_raises_rfc7807_error():
    store = MemoryCounterStore()
    import payday.core.ratelimit as ratelimit_module

    monkey_patched = ratelimit_module
    monkey_patched._limiter = RateLimiter(store)

    try:
        for expected in (1, 2):
            decision = await enforce_rate_limit("rate:test:key", limit=2, window_seconds=60)
            assert decision.allowed is True

        with pytest.raises(RateLimitError) as exc_info:
            await enforce_rate_limit("rate:test:key", limit=2, window_seconds=60)

        error = exc_info.value
        assert error.status_code == 429
        assert error.code == "RATE_LIMITED"
        assert "Retry-After" in error.headers
        assert error.extra["limit"] == 2
    finally:
        monkey_patched._limiter = None


# --- fail-closed configuration ---------------------------------------------


def test_production_rejects_memory_backend():
    with pytest.raises(RuntimeError, match="COUNTER_BACKEND=redis"):
        validate_counter_configuration("production", "memory", redis_required=False)


def test_redis_required_rejects_memory_backend():
    with pytest.raises(RuntimeError, match="COUNTER_BACKEND=redis"):
        validate_counter_configuration("development", "memory", redis_required=True)


def test_valid_configurations_pass():
    # Dev/test may use the in-memory shim explicitly.
    validate_counter_configuration("development", "memory", redis_required=False)
    # Production with Redis passes config validation (availability is checked
    # separately at startup).
    validate_counter_configuration("production", "redis", redis_required=True)


@pytest.mark.asyncio
async def test_unreachable_redis_fails_closed():
    # Port 1 is closed on every host: connection refused (or a fast timeout).
    assert await check_redis_connection("redis://127.0.0.1:1/0") is False


@pytest.mark.asyncio
async def test_startup_gate_raises_when_redis_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "COUNTER_BACKEND", "redis")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "REDIS_REQUIRED", True)

    with pytest.raises(RuntimeError, match="unreachable"):
        await ensure_counters_ready()


@pytest.mark.asyncio
async def test_health_reports_counter_backend(client):
    res = await client.get("/api/v1/public/health")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["status"] == "UP"
    assert data["redis"]["status"] == "UP"
    assert data["redis"]["backend"] == "memory"


# --- optional real-Redis integration (runs in CI against redis:7) ----------

REDIS_TEST_URL = os.environ.get("PAYDAY_TEST_REDIS_URL", "")


@pytest.mark.skipif(
    not REDIS_TEST_URL,
    reason="Set PAYDAY_TEST_REDIS_URL (CI: redis:7 service container) to run",
)
@pytest.mark.asyncio
async def test_real_redis_shared_budget():
    import redis.asyncio as aioredis

    client_a = aioredis.from_url(REDIS_TEST_URL, decode_responses=True)
    client_b = aioredis.from_url(REDIS_TEST_URL, decode_responses=True)
    store_a = RedisCounterStore(client_a)
    store_b = RedisCounterStore(client_b)
    try:
        assert await store_a.ping() is True
        assert (await store_a.incr("rate:real:test", 60)).count == 1
        assert (await store_b.incr("rate:real:test", 60)).count == 2
        assert await store_b.get("rate:real:test") == 2
    finally:
        await client_a.aclose()
        await client_b.aclose()
