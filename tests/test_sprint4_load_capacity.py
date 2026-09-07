"""Sprint 4 — Load & Capacity Characterisation.

METHODOLOGY
-----------
Two distinct things are measured, because conflating them produces numbers
that mean nothing:

* **Serial latency** — requests issued one at a time. This is the true
  per-request service cost (routing, validation, ORM, serialisation).
* **Concurrent throughput** — N requests dispatched at once, measured as
  aggregate wall-clock time and requests/second.

An earlier draft of this file timed each request inside a 200-way
`asyncio.gather` and reported a "p95" of 641 ms. That figure was almost
entirely queueing delay: the test harness shares one SQLite connection via
`StaticPool`, so concurrent requests serialise and each one's stopwatch
includes the time it spent waiting behind the other 199. Latency percentiles
are therefore only taken from the serial measurement.

SCOPE
-----
These run in-process over ASGI against SQLite. They characterise application
cost with network and PostgreSQL latency excluded, and act as a regression
guard against algorithmic blow-ups (N+1 queries, missing indexes). They are
not a substitute for a load test against production infrastructure. See
docs/reports/SPRINT_4_REPORT.md for what this does and does not establish.
"""
from __future__ import annotations

import asyncio
import statistics
import time

import pytest
from httpx import AsyncClient

from payday.models.user import User

# SLOs, set generously above observed values so shared CI runners don't flake.
SERIAL_P95_SLO_MS = 60.0
SERIAL_P99_SLO_MS = 120.0
MIN_THROUGHPUT_RPS = 40.0


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "min": ordered[0],
        "p50": statistics.median(ordered),
        "p95": ordered[max(0, int(len(ordered) * 0.95) - 1)],
        "p99": ordered[max(0, int(len(ordered) * 0.99) - 1)],
        "max": ordered[-1],
    }


def _report_latency(label: str, samples: list[float]) -> dict[str, float]:
    pct = _percentiles(samples)
    print(
        f"\n[serial-latency] {label}: n={len(samples)} "
        f"min={pct['min']:.2f} p50={pct['p50']:.2f} "
        f"p95={pct['p95']:.2f} p99={pct['p99']:.2f} max={pct['max']:.2f} (ms)"
    )
    return pct


async def _measure_serial(request_fn, iterations: int) -> tuple[list[float], set[int]]:
    """Issue `iterations` requests one at a time; return latencies and statuses."""
    latencies: list[float] = []
    statuses: set[int] = set()
    for _ in range(iterations):
        start = time.perf_counter()
        response = await request_fn()
        latencies.append((time.perf_counter() - start) * 1000.0)
        statuses.add(response.status_code)
    return latencies, statuses


@pytest.mark.asyncio
async def test_wallet_balance_serial_latency(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
) -> None:
    """Per-request cost of the single most-called authenticated endpoint.

    Every mobile app foreground event hits this, so it dominates production
    read traffic.
    """
    latencies, statuses = await _measure_serial(
        lambda: client.get("/api/v1/wallet/balance", headers=user_auth_headers),
        iterations=100,
    )

    assert statuses == {200}, f"Non-200 responses: {statuses}"

    pct = _report_latency("GET /wallet/balance", latencies)
    assert pct["p95"] < SERIAL_P95_SLO_MS, f"p95 {pct['p95']:.2f}ms exceeds {SERIAL_P95_SLO_MS}ms"
    assert pct["p99"] < SERIAL_P99_SLO_MS, f"p99 {pct['p99']:.2f}ms exceeds {SERIAL_P99_SLO_MS}ms"


@pytest.mark.asyncio
async def test_public_fee_calculator_serial_latency(client: AsyncClient) -> None:
    """The landing-page fee simulator is unauthenticated and internet-facing.

    It is the most exposed endpoint in the system, so it must stay cheap and
    must not touch the database.
    """
    latencies, statuses = await _measure_serial(
        lambda: client.post(
            "/api/v1/public/fee-calculator",
            json={"type": "DEPOSIT", "channel": "MTN", "amount": 25000.00},
        ),
        iterations=100,
    )

    assert statuses == {200}, f"Non-200 responses: {statuses}"

    pct = _report_latency("POST /public/fee-calculator", latencies)
    # Pure arithmetic, no I/O — should be the fastest endpoint in the system.
    assert pct["p95"] < 25.0, f"Fee calculator p95 {pct['p95']:.2f}ms is unexpectedly expensive"


@pytest.mark.asyncio
async def test_health_probe_is_cheap_enough_for_orchestrator_polling(
    client: AsyncClient,
) -> None:
    """Docker/Kubernetes poll this every 30s per replica; it must never be costly."""
    latencies, statuses = await _measure_serial(
        lambda: client.get("/api/v1/public/health"),
        iterations=100,
    )

    assert statuses == {200}
    pct = _report_latency("GET /public/health", latencies)
    assert pct["p95"] < 25.0, f"Health probe p95 {pct['p95']:.2f}ms is too expensive"


@pytest.mark.asyncio
async def test_transaction_history_does_not_degrade_superlinearly(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
) -> None:
    """History latency must scale sub-linearly as the ledger grows.

    Latency is sampled on an almost-empty ledger, then again after seeding
    40 transactions. A missing index or an N+1 query shows up here as a
    growth factor far above 1x, because the page size is constant at 20 rows.
    """
    baseline, statuses = await _measure_serial(
        lambda: client.get(
            "/api/v1/wallet/transactions?page=1&page_size=20", headers=user_auth_headers
        ),
        iterations=20,
    )
    assert statuses == {200}

    for i in range(40):
        await client.post(
            "/api/v1/wallet/deposit",
            json={"channel": "MTN", "amount": 1000.00 + i, "phone_number": "+237677001122"},
            headers=user_auth_headers,
        )

    loaded, statuses = await _measure_serial(
        lambda: client.get(
            "/api/v1/wallet/transactions?page=1&page_size=20", headers=user_auth_headers
        ),
        iterations=20,
    )
    assert statuses == {200}

    base_p50 = _report_latency("GET /wallet/transactions (empty)", baseline)["p50"]
    load_p50 = _report_latency("GET /wallet/transactions (40 rows)", loaded)["p50"]

    growth = load_p50 / base_p50 if base_p50 > 0 else 1.0
    print(f"[scaling] history p50 growth factor: {growth:.2f}x")

    assert load_p50 < SERIAL_P95_SLO_MS, f"History p50 {load_p50:.2f}ms exceeds budget"
    # A constant-size page over 40 rows should not cost several times more.
    assert growth < 6.0, f"History latency grew {growth:.2f}x — suspect a missing index or N+1"


@pytest.mark.asyncio
async def test_sustained_concurrent_read_throughput(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
) -> None:
    """Aggregate throughput with 200 in-flight reads, and zero errors.

    Reported as requests/second over total wall-clock, which is the meaningful
    figure under a serialising connection pool. Per-request percentiles are
    deliberately not asserted here — see the module docstring.
    """
    total = 200

    start = time.perf_counter()
    responses = await asyncio.gather(
        *(
            client.get("/api/v1/wallet/balance", headers=user_auth_headers)
            for _ in range(total)
        )
    )
    wall_s = time.perf_counter() - start

    statuses = {r.status_code for r in responses}
    assert statuses == {200}, f"Errors under concurrent load: {statuses}"

    rps = total / wall_s
    print(
        f"\n[throughput] {total} concurrent GET /wallet/balance in "
        f"{wall_s:.2f}s -> {rps:.1f} req/s (harness shares one SQLite connection)"
    )

    assert rps > MIN_THROUGHPUT_RPS, f"Throughput {rps:.1f} req/s is below {MIN_THROUGHPUT_RPS}"


@pytest.mark.asyncio
async def test_no_request_is_dropped_under_burst(
    client: AsyncClient,
    test_user: User,
    user_auth_headers: dict,
) -> None:
    """A burst across mixed endpoints must return a complete, correct response set.

    Guards against event-loop starvation and cross-request state bleed — a
    balance read must never observe another request's session.
    """
    burst = []
    for _ in range(60):
        burst.append(client.get("/api/v1/wallet/balance", headers=user_auth_headers))
        burst.append(client.get("/api/v1/public/health"))
        burst.append(
            client.post(
                "/api/v1/public/fee-calculator",
                json={"type": "WITHDRAW", "channel": "ORANGE", "amount": 5000.00},
            )
        )

    responses = await asyncio.gather(*burst, return_exceptions=True)

    failures = [r for r in responses if isinstance(r, Exception)]
    assert not failures, f"{len(failures)} requests raised: {failures[:3]}"

    assert len(responses) == 180
    assert {r.status_code for r in responses} == {200}

    # Every balance response must report the same, correct figure.
    balances = {
        r.json()["data"]["balance"]
        for r in responses
        if r.request.url.path == "/api/v1/wallet/balance"
    }
    assert len(balances) == 1, f"Balance read returned inconsistent values: {balances}"
