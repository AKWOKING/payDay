"""Sprint 4 — Migration / Model Parity.

The rest of the suite builds its schema with `Base.metadata.create_all()`
(see `tests/conftest.py`), i.e. straight from the declarative models.
Production, by contrast, is provisioned with `alembic upgrade head`.

Nothing verified that those two agree — and they did not. `001_initial_schema`
declared `transactions.linked_account_id` NOT NULL while the model declares it
nullable, and omitted 18 of the 22 model indexes. Every test passed regardless,
because no test ever touched the migrated schema.

These tests close that blind spot by running the real migration chain and
diffing the result against the model metadata.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

from payday.core.database import Base
from payday.models import *  # noqa: F401,F403 — register every model on Base.metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_path: Path) -> tuple[Config, str]:
    """Alembic config pointed at a throwaway SQLite file."""
    async_url = f"sqlite+aiosqlite:///{db_path}"
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", async_url)
    # alembic/env.py reads DATABASE_URL first.
    os.environ["DATABASE_URL"] = async_url
    return cfg, async_url


def _migrate_to_head(db_path: Path) -> None:
    """Run the full migration chain. Kept synchronous.

    `alembic/env.py` calls `asyncio.run()`, which raises if a loop is already
    running — so these must not be async tests.
    """
    previous = os.environ.get("DATABASE_URL")
    cfg, _ = _alembic_config(db_path)
    try:
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def test_migrations_produce_schema_identical_to_models(tmp_path: Path) -> None:
    """`alembic upgrade head` must yield exactly the model schema.

    This is the regression guard for the 001 drift. A non-empty diff means the
    migrations and the models have parted ways again.
    """
    db_path = tmp_path / "parity.db"
    _migrate_to_head(db_path)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()

    # `alembic_version` is Alembic's own bookkeeping table and is absent from
    # the model metadata; ignore it, flag everything else.
    def _is_bookkeeping(entry) -> bool:
        text = str(entry)
        return "alembic_version" in text

    real_diff = [d for d in diff if not _is_bookkeeping(d)]

    assert not real_diff, (
        "Migrations have drifted from the models. "
        "Run `alembic revision --autogenerate` and commit the result.\n"
        + "\n".join(f"  - {d}" for d in real_diff)
    )


def test_every_model_index_exists_in_migrated_schema(tmp_path: Path) -> None:
    """All 22 model-declared indexes must survive the migration chain.

    The missing ones were on `transactions.wallet_id`, `.status`, `.type` and
    `.external_ref` — the predicates behind transaction history, the
    reconciliation sweep and webhook lookups. Their absence turns each of
    those into a sequential scan.
    """
    db_path = tmp_path / "indexes.db"
    _migrate_to_head(db_path)

    expected: set[str] = {
        index.name
        for table in Base.metadata.sorted_tables
        for index in table.indexes
    }

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        inspector = sa.inspect(engine)
        actual: set[str] = {
            ix["name"]
            for table in inspector.get_table_names()
            for ix in inspector.get_indexes(table)
            if ix.get("name")
        }
    finally:
        engine.dispose()

    missing = expected - actual
    assert not missing, f"Migration is missing {len(missing)} model indexes: {sorted(missing)}"


def test_idempotency_key_uniqueness_survives_migration(tmp_path: Path) -> None:
    """The UNIQUE index on `idempotency_key` is the anti-double-spend guard.

    A SQLite batch rebuild in 002 will silently drop indexes that were created
    outside the table definition, so assert the constraint is genuinely
    enforced rather than merely present by name.
    """
    db_path = tmp_path / "idem.db"
    _migrate_to_head(db_path)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        inspector = sa.inspect(engine)
        idem = [
            ix
            for ix in inspector.get_indexes("transactions")
            if ix["name"] == "ix_transactions_idempotency_key"
        ]
        assert idem, "ix_transactions_idempotency_key is missing after migration"
        assert idem[0]["unique"], "idempotency_key index exists but is NOT UNIQUE"

        # Prove enforcement, not just declaration.
        wallet_id = _seed_wallet(engine)
        _insert_transaction(engine, wallet_id, idempotency_key="DUPLICATE-KEY-001")
        with pytest.raises(sa.exc.IntegrityError):
            _insert_transaction(engine, wallet_id, idempotency_key="DUPLICATE-KEY-001")
    finally:
        engine.dispose()


def test_transaction_accepts_null_linked_account_on_migrated_schema(
    tmp_path: Path,
) -> None:
    """Regression: a transaction with no linked external account must INSERT.

    `services/transaction_manager.py` sets
    `linked_account_id = linked_account.linked_account_id if linked_account
    else None`. Against the original migration that column was NOT NULL, so
    this INSERT raised IntegrityError and the request 500'd — on PostgreSQL
    only, and only in production.
    """
    db_path = tmp_path / "nullfk.db"
    _migrate_to_head(db_path)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        wallet_id = _seed_wallet(engine)
        _insert_transaction(
            engine,
            wallet_id,
            idempotency_key="NO-LINKED-ACCOUNT-001",
            linked_account_id=None,
        )

        with engine.connect() as conn:
            stored = conn.execute(
                sa.text(
                    "SELECT linked_account_id FROM transactions "
                    "WHERE idempotency_key = :k"
                ),
                {"k": "NO-LINKED-ACCOUNT-001"},
            ).scalar_one()
        assert stored is None
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _seed_wallet(engine: sa.Engine) -> str:
    """Insert a user + wallet directly via SQL and return the wallet id."""
    user_id = str(uuid.uuid4())
    wallet_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (user_id, full_name, phone_number, email, "
                "password_hash, kyc_status, role, status, created_at, updated_at) "
                "VALUES (:id, :name, :phone, :email, :pw, :kyc, :role, :st, :c, :u)"
            ),
            {
                "id": user_id,
                "name": "Parity Probe",
                "phone": f"+2376{uuid.uuid4().int % 10**8:08d}",
                "email": f"{uuid.uuid4().hex[:10]}@payday.cm",
                "pw": "x" * 60,
                "kyc": "VERIFIED",
                "role": "CUSTOMER",
                "st": "ACTIVE",
                "c": now,
                "u": now,
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO wallets (wallet_id, user_id, balance, locked_balance, "
                "currency, status, daily_limit, monthly_limit, created_at, updated_at) "
                "VALUES (:w, :u, :b, :l, :cur, :st, :d, :m, :c, :up)"
            ),
            {
                "w": wallet_id,
                "u": user_id,
                "b": str(Decimal("50000.00")),
                "l": str(Decimal("0.00")),
                "cur": "XAF",
                "st": "ACTIVE",
                "d": str(Decimal("500000.00")),
                "m": str(Decimal("5000000.00")),
                "c": now,
                "up": now,
            },
        )
    return wallet_id


def _insert_transaction(
    engine: sa.Engine,
    wallet_id: str,
    *,
    idempotency_key: str,
    linked_account_id: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO transactions (transaction_id, idempotency_key, wallet_id, "
                "linked_account_id, type, channel, amount, fee, net_amount, status, "
                "created_at, updated_at) "
                "VALUES (:t, :k, :w, :la, :ty, :ch, :a, :f, :n, :s, :c, :u)"
            ),
            {
                "t": str(uuid.uuid4()),
                "k": idempotency_key,
                "w": wallet_id,
                "la": linked_account_id,
                "ty": "DEPOSIT",
                "ch": "MTN",
                "a": str(Decimal("1000.00")),
                "f": str(Decimal("25.00")),
                "n": str(Decimal("975.00")),
                "s": "PENDING",
                "c": now,
                "u": now,
            },
        )
