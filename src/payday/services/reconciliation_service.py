import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Optional, List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from payday.models.transaction import Transaction, TransactionChannel, TransactionStatus
from payday.schemas.admin import (
    ReconciliationRequest,
    ReconciliationReportResponse,
    ReconciliationMismatchItem,
    PartnerStatementItem,
)
from payday.core.logging import logger


class ReconciliationService:
    """
    Automated Financial Reconciliation Engine:
    Compares internal PayDay transaction ledger totals against external partner
    settlement files (MTN MoMo, Orange Money) to detect amount variances,
    status conflicts, and un-settled records.
    """

    @staticmethod
    async def run_reconciliation(
        db: AsyncSession,
        req: ReconciliationRequest,
    ) -> ReconciliationReportResponse:
        report_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # 1. Query Internal Transactions for Channel
        query = select(Transaction).where(Transaction.channel == req.channel)
        if req.start_date:
            start_dt = datetime.combine(req.start_date, datetime.min.time(), tzinfo=timezone.utc)
            query = query.where(Transaction.created_at >= start_dt)
        if req.end_date:
            end_dt = datetime.combine(req.end_date, datetime.max.time(), tzinfo=timezone.utc)
            query = query.where(Transaction.created_at <= end_dt)

        result = await db.execute(query)
        internal_txs = list(result.scalars().all())

        # Aggregate internal figures
        total_internal_txs = len(internal_txs)
        total_internal_vol = sum((tx.amount for tx in internal_txs), Decimal("0.00"))
        total_internal_fees = sum((tx.fee for tx in internal_txs), Decimal("0.00"))

        mismatches: List[ReconciliationMismatchItem] = []
        matched_count = 0

        # If partner records are not provided, simulate standard partner settlement feed from external_refs
        partner_records = req.partner_records
        if partner_records is None:
            partner_records = [
                PartnerStatementItem(
                    external_ref=tx.external_ref or f"AUTO-{tx.transaction_id[:8]}",
                    amount=tx.amount,
                    currency="XAF",
                    status="SUCCESS" if tx.status == TransactionStatus.SUCCESS else "FAILED",
                    channel=req.channel.value,
                )
                for tx in internal_txs
                if tx.status in (TransactionStatus.SUCCESS, TransactionStatus.FAILED)
            ]

        total_partner_txs = len(partner_records)
        total_partner_vol = sum((p.amount for p in partner_records), Decimal("0.00"))

        # Map internal transactions by external_ref and transaction_id
        internal_by_ref: Dict[str, Transaction] = {}
        for tx in internal_txs:
            if tx.external_ref:
                internal_by_ref[tx.external_ref] = tx
            internal_by_ref[tx.transaction_id] = tx

        matched_partner_refs = set()

        # 2. Compare Partner Records against Internal Records
        for p in partner_records:
            tx = internal_by_ref.get(p.external_ref)
            if not tx:
                mismatches.append(
                    ReconciliationMismatchItem(
                        mismatch_type="MISSING_IN_INTERNAL",
                        external_ref=p.external_ref,
                        partner_amount=p.amount,
                        partner_status=p.status,
                        description=f"Partner settlement record {p.external_ref} was not found in PayDay database.",
                    )
                )
                continue

            matched_partner_refs.add(p.external_ref)
            if tx.external_ref:
                matched_partner_refs.add(tx.external_ref)

            # Verify Amount
            if Decimal(str(tx.amount)) != Decimal(str(p.amount)):
                mismatches.append(
                    ReconciliationMismatchItem(
                        mismatch_type="AMOUNT_MISMATCH",
                        transaction_id=tx.transaction_id,
                        external_ref=p.external_ref,
                        internal_amount=tx.amount,
                        partner_amount=p.amount,
                        internal_status=tx.status.value,
                        partner_status=p.status,
                        description=f"Amount mismatch: PayDay={tx.amount} XAF vs Partner={p.amount} XAF.",
                    )
                )
                continue

            # Verify Status
            partner_status_norm = "SUCCESS" if p.status.upper() in ("SUCCESS", "SUCCESSFUL") else "FAILED"
            internal_status_norm = "SUCCESS" if tx.status == TransactionStatus.SUCCESS else ("FAILED" if tx.status == TransactionStatus.FAILED else tx.status.value)

            if internal_status_norm != partner_status_norm:
                mismatches.append(
                    ReconciliationMismatchItem(
                        mismatch_type="STATUS_MISMATCH",
                        transaction_id=tx.transaction_id,
                        external_ref=p.external_ref,
                        internal_amount=tx.amount,
                        partner_amount=p.amount,
                        internal_status=tx.status.value,
                        partner_status=p.status,
                        description=f"Status mismatch: PayDay={tx.status.value} vs Partner={p.status}.",
                    )
                )
                continue

            matched_count += 1

        # 3. Check for Internal Transactions Missing in Partner Feed
        for tx in internal_txs:
            if tx.status == TransactionStatus.SUCCESS and (tx.external_ref not in matched_partner_refs and tx.transaction_id not in matched_partner_refs):
                mismatches.append(
                    ReconciliationMismatchItem(
                        mismatch_type="MISSING_IN_PARTNER",
                        transaction_id=tx.transaction_id,
                        external_ref=tx.external_ref,
                        internal_amount=tx.amount,
                        internal_status=tx.status.value,
                        description=f"Successful internal transaction {tx.transaction_id} is absent from partner settlement feed.",
                    )
                )

        is_balanced = (len(mismatches) == 0 and total_internal_txs > 0)

        logger.info(
            f"[RECONCILIATION] Completed report {report_id} for {req.channel.value}: "
            f"{matched_count} matched, {len(mismatches)} mismatches (Balanced: {is_balanced})"
        )

        return ReconciliationReportResponse(
            report_id=report_id,
            channel=req.channel,
            generated_at=now,
            period_start=req.start_date,
            period_end=req.end_date,
            total_internal_transactions=total_internal_txs,
            total_internal_volume=total_internal_vol,
            total_internal_fees=total_internal_fees,
            total_partner_transactions=total_partner_txs,
            total_partner_volume=total_partner_vol,
            matched_count=matched_count,
            mismatches_count=len(mismatches),
            is_balanced=is_balanced,
            mismatches=mismatches,
        )


reconciliation_service = ReconciliationService()
