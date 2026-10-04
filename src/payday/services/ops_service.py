"""Operational overview — the layer that watches the money path.

Why this exists
---------------
A8 made settlement authoritative (callbacks are verified, and a sweep re-queries
the operator when a notification is lost). But nothing *reports* on that path:
if the sweep stops running, or an operator starts failing every withdrawal, or
transactions pile up in PROCESSING, the first observer is a customer.

The three questions an on-call engineer actually asks at 02:00 are:
  1. Is money stuck? (how many transactions are PROCESSING longer than the sweep
     itself would tolerate, and the age of the oldest)
  2. Is one operator broken, or all of them? (counts per channel)
  3. Is the machine that fixes it still switched on? (sweep configuration)

Everything here is a read-only aggregate over the ledger, so it is safe to call
during an incident. "Stuck" deliberately uses the same definition the sweep uses
— `PROCESSING` whose `updated_at` is older than the threshold — so an operator
sees exactly the backlog the sweep is (or is not) clearing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from payday.core.config import settings
from payday.models.transaction import Transaction, TransactionStatus


@dataclass
class StuckProcessing:
    count: int = 0
    oldest_age_seconds: int | None = None
    threshold_seconds: int = 0


@dataclass
class OpsOverview:
    environment: str
    telco_mode: str
    version: str
    server_time: datetime
    transactions_by_status: dict[str, int] = field(default_factory=dict)
    transactions_by_channel: dict[str, dict[str, int]] = field(default_factory=dict)
    stuck_processing: StuckProcessing = field(default_factory=StuckProcessing)
    sweep: dict = field(default_factory=dict)


async def _counts_by_status(db: AsyncSession) -> dict[str, int]:
    result = await db.execute(
        select(Transaction.status, func.count()).group_by(Transaction.status)
    )
    return {status.value: count for status, count in result.all()}


async def _counts_by_channel(db: AsyncSession) -> dict[str, dict[str, int]]:
    """Per-channel counts, so a single failing operator is distinguishable.

    A total error rate hides the case that matters most in a two-operator market:
    one integration broken while the other is healthy.
    """
    result = await db.execute(
        select(Transaction.channel, Transaction.status, func.count()).group_by(
            Transaction.channel, Transaction.status
        )
    )
    by_channel: dict[str, dict[str, int]] = {}
    for channel, status, count in result.all():
        by_channel.setdefault(channel.value, {})[status.value] = count
    return by_channel


async def _stuck_processing(db: AsyncSession, threshold_seconds: int) -> StuckProcessing:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold_seconds)
    result = await db.execute(
        select(func.count(), func.min(Transaction.updated_at)).where(
            Transaction.status == TransactionStatus.PROCESSING,
            Transaction.updated_at <= cutoff,
        )
    )
    count, oldest = result.one()

    oldest_age: int | None = None
    if count and oldest is not None:
        # SQLite returns naive datetimes for timezone-aware columns; normalise
        # before subtracting so the age is computed rather than crashing in a
        # test environment that is not PostgreSQL.
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        oldest_age = max(0, int((datetime.now(timezone.utc) - oldest).total_seconds()))

    return StuckProcessing(
        count=count or 0, oldest_age_seconds=oldest_age, threshold_seconds=threshold_seconds
    )


async def build_overview(
    db: AsyncSession, *, stuck_after_seconds: int | None = None
) -> OpsOverview:
    threshold = (
        settings.OPS_STUCK_PROCESSING_SECONDS
        if stuck_after_seconds is None
        else stuck_after_seconds
    )
    return OpsOverview(
        environment=settings.ENVIRONMENT,
        telco_mode=settings.TELCO_MODE,
        version=settings.VERSION,
        server_time=datetime.now(timezone.utc),
        transactions_by_status=await _counts_by_status(db),
        transactions_by_channel=await _counts_by_channel(db),
        stuck_processing=await _stuck_processing(db, threshold),
        sweep={
            "enabled": settings.TELCO_STATUS_SWEEP_ENABLED,
            "interval_seconds": settings.TELCO_STATUS_SWEEP_INTERVAL_SECONDS,
            "min_age_seconds": settings.TELCO_STATUS_SWEEP_MIN_AGE_SECONDS,
            "batch_size": settings.TELCO_STATUS_SWEEP_BATCH_SIZE,
        },
    )
