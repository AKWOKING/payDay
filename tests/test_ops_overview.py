"""Operational visibility: the layer that notices money is stuck.

A8 made settlement authoritative and the sweep clears lost callbacks — but
nothing reported on that path. If the sweep stopped running or one operator
began failing every withdrawal, the first observer would be a customer. This
endpoint answers the three questions an on-call engineer asks:

  1. Is money stuck? (PROCESSING transactions older than the sweep's own window)
  2. Is one operator broken, or all of them? (counts per channel)
  3. Is the thing that fixes it switched on? (sweep configuration)

The tests seed transactions directly, because the point is the *aggregate*, not
the path that created each row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from payday.core.config import settings
from payday.models.transaction import (
    Transaction,
    TransactionChannel,
    TransactionStatus,
    TransactionType,
)
from payday.models.wallet import Wallet
from payday.models.user import User


def _tx(
    wallet_id: str,
    *,
    key: str,
    status: TransactionStatus,
    channel: TransactionChannel = TransactionChannel.MTN,
    age_seconds: int = 0,
) -> Transaction:
    updated = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return Transaction(
        idempotency_key=key,
        wallet_id=wallet_id,
        type=TransactionType.WITHDRAW,
        channel=channel,
        amount=1000,
        fee=10,
        net_amount=990,
        status=status,
        created_at=updated,
        updated_at=updated,
    )


async def _wallet_id(db_session: AsyncSession, test_user: User) -> str:
    result = await db_session.execute(
        Wallet.__table__.select().where(Wallet.user_id == test_user.user_id)
    )
    return result.first().wallet_id


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


async def test_ops_overview_requires_authentication(client: AsyncClient):
    res = await client.get("/api/v1/admin/ops/overview")
    assert res.status_code == 401


async def test_ops_overview_rejects_a_normal_user(client: AsyncClient, user_auth_headers):
    """A customer must not be able to enumerate the operator's failure rates."""
    res = await client.get("/api/v1/admin/ops/overview", headers=user_auth_headers)
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# The answers it gives
# ---------------------------------------------------------------------------


async def test_ops_overview_reports_counts_per_channel(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession, test_user: User
):
    wallet_id = await _wallet_id(db_session, test_user)
    db_session.add_all(
        [
            _tx(wallet_id, key="ops-1", status=TransactionStatus.SUCCESS),
            _tx(wallet_id, key="ops-2", status=TransactionStatus.FAILED,
                channel=TransactionChannel.ORANGE),
            _tx(wallet_id, key="ops-3", status=TransactionStatus.PROCESSING,
                channel=TransactionChannel.ORANGE),
        ]
    )
    await db_session.commit()

    res = await client.get("/api/v1/admin/ops/overview", headers=admin_auth_headers)

    assert res.status_code == 200
    data = res.json()["data"]
    assert data["transactions_by_status"]["SUCCESS"] == 1
    assert data["transactions_by_status"]["FAILED"] == 1
    assert data["transactions_by_status"]["PROCESSING"] == 1
    # Per-channel detail exists so "Orange is down" is distinguishable from
    # "everything is down".
    assert data["transactions_by_channel"]["ORANGE"]["FAILED"] == 1
    assert data["transactions_by_channel"]["MTN"]["SUCCESS"] == 1


async def test_stuck_processing_uses_the_sweep_window(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession, test_user: User
):
    """Only PROCESSING rows older than the threshold are 'stuck'.

    A freshly PROCESSING transaction is normal — a withdrawal the operator has
    not confirmed yet. Counting it would make the alert fire on every payment.
    """
    wallet_id = await _wallet_id(db_session, test_user)
    db_session.add_all(
        [
            _tx(wallet_id, key="ops-fresh", status=TransactionStatus.PROCESSING,
                age_seconds=60),
            _tx(wallet_id, key="ops-stale", status=TransactionStatus.PROCESSING,
                age_seconds=3600),
        ]
    )
    await db_session.commit()

    res = await client.get(
        "/api/v1/admin/ops/overview?stuck_after_seconds=900", headers=admin_auth_headers
    )

    assert res.status_code == 200
    stuck = res.json()["data"]["stuck_processing"]
    assert stuck["count"] == 1
    assert stuck["threshold_seconds"] == 900
    assert stuck["oldest_age_seconds"] >= 3600


async def test_stuck_threshold_is_overridable(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession, test_user: User
):
    """A 60-second threshold on the same data catches both rows."""
    wallet_id = await _wallet_id(db_session, test_user)
    db_session.add_all(
        [
            _tx(wallet_id, key="ops-fresh2", status=TransactionStatus.PROCESSING,
                age_seconds=60),
            _tx(wallet_id, key="ops-stale2", status=TransactionStatus.PROCESSING,
                age_seconds=3600),
        ]
    )
    await db_session.commit()

    res = await client.get(
        "/api/v1/admin/ops/overview?stuck_after_seconds=60", headers=admin_auth_headers
    )

    assert res.json()["data"]["stuck_processing"]["count"] == 2


async def test_successful_transactions_are_never_reported_as_stuck(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession, test_user: User
):
    """The status, not the age, decides. An old settled row is not a problem."""
    wallet_id = await _wallet_id(db_session, test_user)
    db_session.add(
        _tx(wallet_id, key="ops-old-success", status=TransactionStatus.SUCCESS,
            age_seconds=86400)
    )
    await db_session.commit()

    res = await client.get("/api/v1/admin/ops/overview", headers=admin_auth_headers)

    assert res.json()["data"]["stuck_processing"]["count"] == 0
    assert res.json()["data"]["stuck_processing"]["oldest_age_seconds"] is None


async def test_sweep_configuration_is_visible(
    client: AsyncClient, admin_auth_headers
):
    """'Is the safety net switched on?' must be answerable without reading config.

    In production an operator running without the sweep is the failure mode A8's
    whole design assumes is configured; here it is a field, not a guess.
    """
    res = await client.get("/api/v1/admin/ops/overview", headers=admin_auth_headers)

    sweep = res.json()["data"]["sweep"]
    assert sweep["enabled"] == settings.TELCO_STATUS_SWEEP_ENABLED
    assert sweep["interval_seconds"] == settings.TELCO_STATUS_SWEEP_INTERVAL_SECONDS
    assert sweep["min_age_seconds"] == settings.TELCO_STATUS_SWEEP_MIN_AGE_SECONDS
    assert sweep["batch_size"] == settings.TELCO_STATUS_SWEEP_BATCH_SIZE


async def test_threshold_bounds_are_enforced(client: AsyncClient, admin_auth_headers):
    """An unreasonably small threshold would make the alert useless; reject it."""
    res = await client.get(
        "/api/v1/admin/ops/overview?stuck_after_seconds=5", headers=admin_auth_headers
    )
    assert res.status_code == 422


@pytest.mark.parametrize("field", ["environment", "telco_mode", "version", "server_time"])
async def test_overview_identifies_the_environment(
    client: AsyncClient, admin_auth_headers, field: str
):
    """During an incident the first question is 'which environment am I looking at'."""
    res = await client.get("/api/v1/admin/ops/overview", headers=admin_auth_headers)
    assert res.json()["data"][field]
