"""Redis client singleton for PayDay.

Redis backs the shared counters (login throttling, PIN attempt tracking) so
that multiple API replicas enforce one combined budget. The application must
never silently fall back to a process-local store in production — doing so
would quietly reintroduce the single-replica ceiling that these counters exist
to remove. Startup validation (fail-closed) lives in `payday.core.counters`.
"""

from typing import Optional

import redis.asyncio as aioredis
from redis.asyncio import Redis

from payday.core.config import settings

_client: Optional[Redis] = None


def get_redis() -> Redis:
    """Return the process-wide async Redis client (created lazily)."""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
    return _client


async def close_redis() -> None:
    """Close the process-wide client, if one was created."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def redis_ping() -> bool:
    """Best-effort liveness probe used by the health endpoint."""
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False
