"""Infrastructure probes a load balancer can act on, and cache-safety of the API.

Why this file exists
--------------------
`docs/design/INFRASTRUCTURE_SCALING_PLAN.md` records which scaling decisions we
have taken and which we are deliberately deferring. Two of them are enforced
here, because both are the kind of thing that is invisible until the day it
matters:

* **Probes must be able to fail.** The single `/health` endpoint answers 200
  even when Redis is unreachable — deliberately, so it stays a status report.
  That makes it useless to a load balancer or orchestrator, which only reads the
  status code: an instance with a dead dependency keeps receiving traffic.
  `/health/live` answers "should this instance be restarted" (it touches no
  dependency, so a shared outage cannot cause a restart storm) and
  `/health/ready` answers "should this instance receive traffic" (503 when the
  database or counter store is down).

* **No API response may be cached.** A cached balance or receipt is a wrong
  answer about money, and it fails silently. The default is `no-store` on
  everything under `/api/`, including error responses.

These tests monkeypatch the counter store rather than stopping Redis: they
assert our *decision* (what the probe returns when a dependency is down), not
Redis's behaviour.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from payday.api.v1 import public as public_api
from payday.core.config import settings
from payday.core.database import get_db
from payday.main import app


class _DeadCounterStore:
    """A counter store whose backing service is unreachable."""

    async def ping(self) -> bool:
        return False


class _ExplodingCounterStore:
    """A counter store that raises instead of answering — e.g. a closed socket."""

    async def ping(self) -> bool:
        raise ConnectionError("connection refused")


class _BrokenSession:
    """A database session whose connection is gone."""

    async def execute(self, *args, **kwargs):
        raise ConnectionError("database is unreachable")


# ---------------------------------------------------------------------------
# Liveness: never triggered by a dependency every instance also lacks
# ---------------------------------------------------------------------------


async def test_liveness_is_up_and_never_touches_a_dependency(client: AsyncClient, monkeypatch):
    """A liveness probe that pings Redis would restart every instance at once.

    During a shared dependency outage the correct action is to stop sending
    traffic (readiness) — not to restart healthy processes.
    """
    monkeypatch.setattr(public_api, "get_counter_store", lambda: _ExplodingCounterStore())

    res = await client.get("/api/v1/public/health/live")

    assert res.status_code == 200
    body = res.json()
    assert body["data"]["status"] == "UP"


# ---------------------------------------------------------------------------
# Readiness: 503 when the money path cannot be served correctly
# ---------------------------------------------------------------------------


async def test_readiness_is_ready_when_both_dependencies_are_up(client: AsyncClient):
    res = await client.get("/api/v1/public/health/ready")

    assert res.status_code == 200
    data = res.json()["data"]
    assert data["status"] == "READY"
    assert data["checks"]["database"]["status"] == "UP"
    assert data["checks"]["counter_store"]["status"] == "UP"
    assert data["checks"]["counter_store"]["backend"] == settings.COUNTER_BACKEND


async def test_readiness_fails_when_the_counter_store_is_down(client: AsyncClient, monkeypatch):
    """Redis is fail-closed at startup (WS-0); losing it afterwards must be visible."""
    monkeypatch.setattr(public_api, "get_counter_store", lambda: _DeadCounterStore())

    res = await client.get("/api/v1/public/health/ready")

    assert res.status_code == 503
    body = res.json()
    assert body["success"] is False
    assert body["data"]["status"] == "NOT_READY"
    assert body["data"]["checks"]["counter_store"]["status"] == "DOWN"
    # The database check still runs and reports independently — an operator
    # needs to know which dependency is actually down.
    assert body["data"]["checks"]["database"]["status"] == "UP"


async def test_readiness_fails_when_the_counter_store_raises(client: AsyncClient, monkeypatch):
    """A probe must never propagate an exception; it must report and return."""
    monkeypatch.setattr(public_api, "get_counter_store", lambda: _ExplodingCounterStore())

    res = await client.get("/api/v1/public/health/ready")

    assert res.status_code == 503
    assert res.json()["data"]["checks"]["counter_store"]["error"] == "ConnectionError"


async def test_readiness_fails_when_the_database_is_down(client: AsyncClient):
    """Without the database there is no ledger, so the instance must not take traffic."""

    async def _broken_db():
        yield _BrokenSession()

    # Restore whatever override the `client` fixture installed, so the swap
    # cannot leak into another test even if the request raises.
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _broken_db
    try:
        res = await client.get("/api/v1/public/health/ready")
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous

    assert res.status_code == 503
    body = res.json()
    assert body["data"]["checks"]["database"]["status"] == "DOWN"
    # Redis is still up: the probe reports each dependency independently so an
    # operator can tell which one is actually failing.
    assert body["data"]["checks"]["counter_store"]["status"] == "UP"


# ---------------------------------------------------------------------------
# Cache safety
# ---------------------------------------------------------------------------


async def test_public_health_is_not_cacheable(client: AsyncClient):
    res = await client.get("/api/v1/public/health")

    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store"


async def test_money_reads_are_not_cacheable(client: AsyncClient, user_auth_headers):
    """A cached balance is a wrong answer that looks like a real one."""
    for path in ("/api/v1/wallet/balance", "/api/v1/wallet/transactions"):
        res = await client.get(path, headers=user_auth_headers)
        assert res.status_code == 200, f"{path} -> {res.status_code}"
        assert res.headers["cache-control"] == "no-store", f"{path} is cacheable"


async def test_error_responses_are_not_cacheable(client: AsyncClient):
    """An error is a response too; a cached 401/404 poisons later requests."""
    res = await client.get("/api/v1/wallet/balance")

    assert res.status_code == 401
    assert res.headers["cache-control"] == "no-store"


async def test_non_api_paths_are_left_alone(client: AsyncClient):
    """The rule is scoped to the API; the landing page and docs are not money."""
    res = await client.get("/")

    assert res.status_code == 200
    assert "cache-control" not in res.headers
