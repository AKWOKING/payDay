"""Rate limiting over the shared counter store.

WS-3 / LB-6: `POST /auth/login` is throttled on two independent keys —
per-phone-number (an attacker spraying one account across many IPs) and
per-source-IP (an attacker spraying many accounts from one host). The same
limiter is applied to registration and refresh.

The counter is shared with the PIN failure tracker, so on N replicas there is
one combined budget — that shared-budget property is the whole point (LB-4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from payday.core.config import settings
from payday.core.counters import CounterStore, get_counter_store
from payday.core.exceptions import RateLimitError


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    count: int
    limit: int
    window_seconds: int
    retry_after_seconds: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.count)


class RateLimiter:
    """Fixed-window (sliding-TTL) limiter, one increment per request."""

    def __init__(self, store: CounterStore):
        self._store = store

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        result = await self._store.incr(key, window_seconds)
        return RateLimitDecision(
            allowed=result.count <= limit,
            count=result.count,
            limit=limit,
            window_seconds=window_seconds,
            retry_after_seconds=result.ttl_seconds,
        )

    async def reset(self, key: str) -> None:
        """Drop the bucket (used to clear the per-account counter on success)."""
        await self._store.delete(key)


_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_counter_store())
    return _limiter


def reset_rate_limiter() -> None:
    """Drop the singleton so the next call rebuilds it (test isolation)."""
    global _limiter
    _limiter = None


async def enforce_rate_limit(
    key: str, limit: int, window_seconds: int
) -> RateLimitDecision:
    """Increment the bucket and raise RateLimitError (429) when over limit."""
    decision = await get_rate_limiter().hit(key, limit, window_seconds)
    if not decision.allowed:
        raise RateLimitError(
            limit=limit, retry_after=decision.retry_after_seconds
        )
    return decision


async def reset_rate_limit(key: str) -> None:
    await get_rate_limiter().reset(key)


def get_client_ip(request: Request) -> str:
    """Resolve the effective client IP for rate-limit keys.

    `X-Forwarded-For` is honoured only when the direct socket peer is a
    configured trusted proxy; otherwise it is attacker-controlled and ignored,
    so a client cannot reset its own bucket by spoofing the header.
    """
    client = request.client
    peer = client.host if client else "unknown"
    if peer in settings.TRUSTED_PROXY_IPS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


# --- key builders ---------------------------------------------------------


def login_phone_key(phone_number: str) -> str:
    return f"rate:auth:login:phone:{phone_number}"


def login_ip_key(ip: str) -> str:
    return f"rate:auth:login:ip:{ip}"


def register_ip_key(ip: str) -> str:
    return f"rate:auth:register:ip:{ip}"


def refresh_ip_key(ip: str) -> str:
    return f"rate:auth:refresh:ip:{ip}"


def pin_failure_key(user_id: str) -> str:
    return f"pin:fail:{user_id}"
