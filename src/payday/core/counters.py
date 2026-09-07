"""Shared counter store — Redis-backed, with an explicit dev/test shim.

WS-0 / WS-5: every counter that must survive process death or be shared by
multiple replicas (login attempts, PIN failures) lives behind this small
abstraction.

Rules:

- ``COUNTER_BACKEND=redis`` is the production configuration. It connects to the
  configured ``REDIS_URL`` and **fails closed at startup** when unreachable.
- ``COUNTER_BACKEND=memory`` is a process-local store for dev/tests. It is
  selected explicitly by config, never as a fallback when Redis is down.
- In production Redis is required: the app refuses to start with the memory
  backend, because a silent in-process fallback is precisely the
  shared-state bug this module exists to eliminate.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from payday.core.config import settings
from payday.core.redis_client import get_redis


@dataclass(frozen=True)
class IncrementResult:
    """Result of an atomic INCR+EXPIRE.

    ``ttl_seconds`` is the remaining window lifetime, used for ``Retry-After``.
    """

    count: int
    ttl_seconds: int


class CounterStore(Protocol):
    """Minimal contract shared by the Redis and in-memory implementations."""

    async def incr(self, key: str, ttl_seconds: int) -> IncrementResult: ...

    async def get(self, key: str) -> int: ...

    async def delete(self, key: str) -> None: ...

    async def ping(self) -> bool: ...


class RedisCounterStore:
    """Atomic INCR + EXPIRE over ``redis.asyncio``.

    INCR and EXPIRE run as one pipeline on a single connection, so the counter
    is incremented atomically and the window is (re)armed on every hit. Exactly
    one caller observes each increment because INCR is atomic; the pipeline
    guarantees the EXPIRE is issued even if the calling coroutine is
    interrupted between commands.
    """

    def __init__(self, client=None):
        self._client = client if client is not None else get_redis()

    async def incr(self, key: str, ttl_seconds: int) -> IncrementResult:
        pipe = self._client.pipeline(transaction=False)
        pipe.incr(key)
        pipe.expire(key, ttl_seconds)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
        return IncrementResult(count=int(count), ttl_seconds=max(0, int(ttl or 0)))

    async def get(self, key: str) -> int:
        value = await self._client.get(key)
        return int(value) if value is not None else 0

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def ping(self) -> bool:
        return bool(await self._client.ping())


class MemoryCounterStore:
    """Process-local store for dev/tests — explicit backend, never a fallback.

    Mirrors the Redis semantics (sliding TTL window) so behaviour is identical
    on a single process. ``now_fn`` is injectable for deterministic expiry
    tests.
    """

    def __init__(self, now_fn: Callable[[], float] = time.monotonic):
        self._buckets: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()
        self._now = now_fn

    async def incr(self, key: str, ttl_seconds: int) -> IncrementResult:
        now = self._now()
        async with self._lock:
            count, expires = self._buckets.get(key, (0, 0.0))
            if expires <= now:
                count = 0
            count += 1
            expires = now + ttl_seconds
            self._buckets[key] = (count, expires)
            return IncrementResult(count=count, ttl_seconds=max(1, int(expires - now)))

    async def get(self, key: str) -> int:
        now = self._now()
        async with self._lock:
            count, expires = self._buckets.get(key, (0, 0.0))
            if expires <= now:
                self._buckets.pop(key, None)
                return 0
            return count

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._buckets.pop(key, None)

    async def ping(self) -> bool:
        return True

    def clear(self) -> None:
        """Test isolation only — never call in production."""
        self._buckets.clear()


_store: Optional[CounterStore] = None


def get_counter_store() -> CounterStore:
    """Return the process-wide store selected by ``COUNTER_BACKEND``."""
    global _store
    if _store is None:
        if settings.COUNTER_BACKEND == "redis":
            _store = RedisCounterStore()
        else:
            _store = MemoryCounterStore()
    return _store


def reset_counter_store() -> None:
    """Drop the singleton so the next call rebuilds it (test isolation)."""
    global _store
    _store = None


async def check_redis_connection(url: str) -> bool:
    """Ping a Redis URL with a short, dedicated connection. False on failure."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        url, socket_connect_timeout=1.0, socket_timeout=1.0, decode_responses=True
    )
    try:
        await client.ping()
        return True
    except Exception:
        return False
    finally:
        await client.aclose()


def validate_counter_configuration(
    environment: str, backend: str, redis_required: bool
) -> None:
    """Synchronous config sanity check; raises RuntimeError when non-compliant."""
    if environment == "production" and backend != "redis":
        raise RuntimeError(
            "ENVIRONMENT=production requires COUNTER_BACKEND=redis: an in-memory "
            "counter store cannot enforce a shared login/PIN budget across "
            "replicas. Refusing to start (fail-closed)."
        )
    if redis_required and backend != "redis":
        raise RuntimeError(
            "REDIS_REQUIRED=true requires COUNTER_BACKEND=redis. Refusing to "
            "start (fail-closed)."
        )


async def ensure_counters_ready() -> None:
    """Startup gate. Raises RuntimeError when Redis is required but unavailable.

    Called from the FastAPI lifespan so a misconfigured/unreachable store
    prevents the app from serving — never a silent in-memory fallback.
    """
    validate_counter_configuration(
        settings.ENVIRONMENT, settings.COUNTER_BACKEND, settings.REDIS_REQUIRED
    )
    if settings.COUNTER_BACKEND == "redis":
        if not await check_redis_connection(settings.REDIS_URL):
            raise RuntimeError(
                f"Redis is required (COUNTER_BACKEND=redis) but unreachable at "
                f"{settings.REDIS_URL}. Refusing to start (fail-closed)."
            )
