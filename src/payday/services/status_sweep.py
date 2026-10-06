"""Periodic authoritative status sweep (M1 / A8 — the A9 safety net).

Why this exists
---------------
Both operators document notifications that never arrive or arrive late. After
A8, settlement is decided by re-querying the operator's status endpoint, but the
re-query only happens *when a callback arrives*. Without a sweep, a transaction
whose callback is lost stays `PROCESSING` forever: for a deposit that means a
customer who paid and was never credited, with support holding a payment the
system insists is still in flight.

The sweep closes that gap by asking the operator about every transaction that has
been `PROCESSING` for longer than the operator's own confirmation window. It
settles through exactly the same verified path a callback uses
(`transaction_manager.settle_from_provider`), so it cannot credit anything the
operator does not confirm — it is a *pull* instead of a *push*, not a second
opinion.

Design notes
------------
* **Idempotent by construction.** A duplicate answer is `ALREADY_FINAL` and does
  nothing, so running the sweep in more than one replica is wasteful, not
  dangerous.
* **One failure never stops the batch.** An operator error on one transaction is
  logged and the rest still run; a sweep that dies on the first timeout is worse
  than no sweep, because it looks healthy.
* **Mock channels are skipped.** The simulator settles synchronously, so there is
  nothing pending to ask about.
* **Bounded work per pass.** `limit` caps how many transactions one pass touches,
  so a backlog cannot turn into an unbounded burst of operator calls.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payday.core.config import settings
from payday.core.logging import logger
from payday.models.transaction import Transaction, TransactionStatus
from payday.services.transaction_manager import SettlementOutcome, transaction_manager


@dataclass
class SweepReport:
    """What one pass did, for logging and for tests to assert on."""

    examined: int = 0
    settled: int = 0
    already_final: int = 0
    inconclusive: int = 0
    amount_mismatch: int = 0
    skipped_mock: int = 0
    errors: int = 0
    details: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "examined": self.examined,
            "settled": self.settled,
            "already_final": self.already_final,
            "inconclusive": self.inconclusive,
            "amount_mismatch": self.amount_mismatch,
            "skipped_mock": self.skipped_mock,
            "errors": self.errors,
        }


class StatusSweepService:
    """Re-queries the operator for transactions stuck in PROCESSING."""

    def __init__(self, adapter_factory_override=None):
        # Injectable so tests can drive the sweep without an operator.
        self._adapter_factory_override = adapter_factory_override

    @property
    def _factory(self):
        if self._adapter_factory_override is not None:
            return self._adapter_factory_override
        from payday.adapters.factory import adapter_factory

        return adapter_factory

    async def sweep_once(
        self,
        db: AsyncSession,
        *,
        min_age_seconds: Optional[int] = None,
        limit: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> SweepReport:
        """Ask the operator about every stale PROCESSING transaction, once."""
        min_age_seconds = (
            settings.TELCO_STATUS_SWEEP_MIN_AGE_SECONDS
            if min_age_seconds is None
            else min_age_seconds
        )
        limit = settings.TELCO_STATUS_SWEEP_BATCH_SIZE if limit is None else limit
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=min_age_seconds)

        result = await db.execute(
            select(Transaction)
            .where(
                Transaction.status == TransactionStatus.PROCESSING,
                Transaction.updated_at <= cutoff,
            )
            .order_by(Transaction.updated_at)
            .limit(limit)
        )
        stale = result.scalars().all()

        report = SweepReport()
        for transaction in stale:
            report.examined += 1
            try:
                adapter = self._factory.get_adapter(transaction.channel.value)
            except Exception as exc:  # pragma: no cover - misconfiguration
                logger.error(
                    f"[SWEEP] No adapter for {transaction.channel.value}: {exc}"
                )
                report.errors += 1
                continue

            if getattr(adapter, "use_mock", False):
                report.skipped_mock += 1
                continue

            try:
                requery = await adapter.query_status(
                    channel_ref=transaction.external_ref,
                    tx_type=transaction.type.value,
                    order_id=transaction.provider_order_id,
                    amount=transaction.amount,
                )
                outcome, transaction = await transaction_manager.settle_from_provider(
                    db=db,
                    transaction=transaction,
                    provider_status=requery.status,
                    provider_txn_id=requery.provider_txn_id,
                    provider_order_id=requery.provider_order_id,
                    provider_amount=(
                        requery.raw_response.get("amount")
                        if isinstance(requery.raw_response, dict)
                        else None
                    ),
                )
            except Exception as exc:
                # One bad transaction must not abort the batch.
                report.errors += 1
                report.details.append(
                    {"transaction_id": transaction.transaction_id, "error": str(exc)}
                )
                logger.error(
                    f"[SWEEP] {transaction.transaction_id} could not be swept: {exc}"
                )
                continue

            report.details.append(
                {
                    "transaction_id": transaction.transaction_id,
                    "outcome": outcome.value,
                    "provider_status": requery.status,
                }
            )
            if outcome is SettlementOutcome.SETTLED:
                report.settled += 1
                logger.info(
                    f"[SWEEP] Settled {transaction.transaction_id} from provider "
                    f"status {requery.status} (callback never arrived)"
                )
            elif outcome is SettlementOutcome.ALREADY_FINAL:
                report.already_final += 1
            elif outcome is SettlementOutcome.AMOUNT_MISMATCH:
                report.amount_mismatch += 1
            else:
                report.inconclusive += 1

        if report.examined:
            logger.info(f"[SWEEP] pass complete: {report.as_dict()}")
        return report


status_sweep_service = StatusSweepService()


async def run_status_sweep_forever(interval_seconds: Optional[int] = None) -> None:
    """Sweep on an interval until cancelled.

    Started from the application lifespan when `TELCO_STATUS_SWEEP_ENABLED` is
    set. A plain asyncio loop is deliberate: this codebase has no scheduler, and
    introducing one is a bigger decision than this safety net should carry. The
    sweep is idempotent, so a second replica doing the same work is wasteful
    rather than harmful.
    """
    from payday.core.database import AsyncSessionLocal

    interval = interval_seconds or settings.TELCO_STATUS_SWEEP_INTERVAL_SECONDS
    logger.info(f"[SWEEP] status sweep running every {interval}s")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await status_sweep_service.sweep_once(db)
        except asyncio.CancelledError:
            logger.info("[SWEEP] status sweep stopped")
            raise
        except Exception as exc:
            # Never let the loop die: a sweep that stops silently is worse than
            # one that logs and keeps trying.
            logger.error(f"[SWEEP] pass failed: {exc}")
        await asyncio.sleep(interval)
