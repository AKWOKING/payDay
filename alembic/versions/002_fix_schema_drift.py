"""002_fix_schema_drift

Reconcile the Alembic schema with the SQLAlchemy models.

Revision ID: 002_fix_schema_drift
Revises: 001_initial_schema
Create Date: 2026-09-07 00:00:00.000000

WHY THIS MIGRATION EXISTS
-------------------------
`001_initial_schema` drifted from the declarative models in three ways. The
drift was invisible to the test suite because `tests/conftest.py` builds its
schema with `Base.metadata.create_all()` — i.e. from the models — so no test
ever exercised the migration-produced schema.

1. `transactions.linked_account_id` was NOT NULL in the migration but
   `nullable=True` in `models/transaction.py`.

   This is production-breaking. `services/transaction_manager.py` computes
   `linked_account_id = linked_account.linked_account_id if linked_account
   else None` and passes that straight into `Transaction(...)`. Any deposit or
   withdrawal without a linked external account INSERTs NULL and would raise
   IntegrityError/500 on a migrated PostgreSQL database, while passing on the
   model-built SQLite schema used by the tests.

2. The same column's foreign key used `ondelete='RESTRICT'` in the migration
   but `ondelete='SET NULL'` in the model. Under RESTRICT, deleting a linked
   external account that has any transaction history is refused outright,
   which blocks account-unlinking and GDPR-style erasure flows.

3. 18 of the 22 indexes declared on the models were missing. The absent ones
   include `transactions.wallet_id`, `.status`, `.type` and `.external_ref` —
   precisely the predicates used by transaction history, the settlement
   reconciliation sweep and webhook external-reference lookups. On a migrated
   database every one of those becomes a sequential scan.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_fix_schema_drift"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table_name, [columns]) — every index declared on the models
# that 001_initial_schema failed to create.
MISSING_INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_users_kyc_status", "users", ["kyc_status"]),
    ("ix_users_role", "users", ["role"]),
    ("ix_users_status", "users", ["status"]),
    ("ix_audit_logs_action", "audit_logs", ["action"]),
    ("ix_audit_logs_actor_id", "audit_logs", ["actor_id"]),
    ("ix_audit_logs_entity_id", "audit_logs", ["entity_id"]),
    ("ix_linked_external_accounts_provider", "linked_external_accounts", ["provider"]),
    ("ix_linked_external_accounts_user_id", "linked_external_accounts", ["user_id"]),
    ("ix_wallets_status", "wallets", ["status"]),
    ("ix_transactions_channel", "transactions", ["channel"]),
    ("ix_transactions_external_ref", "transactions", ["external_ref"]),
    ("ix_transactions_linked_account_id", "transactions", ["linked_account_id"]),
    ("ix_transactions_status", "transactions", ["status"]),
    ("ix_transactions_type", "transactions", ["type"]),
    ("ix_transactions_wallet_id", "transactions", ["wallet_id"]),
    ("ix_notifications_status", "notifications", ["status"]),
    ("ix_notifications_transaction_id", "notifications", ["transaction_id"]),
    ("ix_notifications_user_id", "notifications", ["user_id"]),
]

# PostgreSQL's default naming convention for the unnamed ForeignKeyConstraint
# emitted by 001_initial_schema.
_PG_FK_NAME = "transactions_linked_account_id_fkey"


def _corrected_transactions_table() -> sa.Table:
    """The `transactions` table as it *should* be, for SQLite batch rebuilds.

    Declared standalone (not imported from the models) so this migration stays
    a fixed historical artefact that will not shift as the models evolve.
    """
    metadata = sa.MetaData()
    return sa.Table(
        "transactions",
        metadata,
        sa.Column("transaction_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("wallet_id", sa.String(length=36), nullable=False),
        # Corrected: nullable.
        sa.Column("linked_account_id", sa.String(length=36), nullable=True),
        sa.Column(
            "type",
            sa.Enum("DEPOSIT", "WITHDRAW", name="transactiontype"),
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("MTN", "ORANGE", "UBA", name="transactionchannel"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("fee", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("net_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "PROCESSING", "SUCCESS", "FAILED", "REVERSED",
                name="transactionstatus",
            ),
            nullable=False,
        ),
        sa.Column("external_ref", sa.String(length=100), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["wallets.wallet_id"], ondelete="RESTRICT"
        ),
        # Corrected: SET NULL rather than RESTRICT.
        sa.ForeignKeyConstraint(
            ["linked_account_id"],
            ["linked_external_accounts.linked_account_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("transaction_id"),
        sa.CheckConstraint("amount > 0.00", name="chk_tx_positive_amount"),
        sa.CheckConstraint("fee >= 0.00", name="chk_tx_positive_fee"),
        # Declared inside the table so the SQLite batch rebuild recreates it.
        # 001 created this index separately; without it here the rebuild would
        # silently drop the UNIQUE guarantee that makes idempotency keys — and
        # therefore the anti-double-spend protection — enforceable.
        sa.Index("ix_transactions_idempotency_key", "idempotency_key", unique=True),
    )


def _existing_index_names(table: str) -> set[str]:
    """Index names already present on `table`, for idempotent (re)runs."""
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table) if ix.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # --- 1 & 2. Nullability and foreign-key delete rule -------------------- #
    # batch_alter_table is a plain ALTER on PostgreSQL and a table rebuild on
    # SQLite, so this is portable across both dialects.
    if dialect == "postgresql":
        op.alter_column(
            "transactions",
            "linked_account_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        # Swap RESTRICT for SET NULL to match the model.
        op.drop_constraint(_PG_FK_NAME, "transactions", type_="foreignkey")
        op.create_foreign_key(
            _PG_FK_NAME,
            "transactions",
            "linked_external_accounts",
            ["linked_account_id"],
            ["linked_account_id"],
            ondelete="SET NULL",
        )
    else:
        # SQLite cannot ALTER nullability or drop an unnamed constraint in
        # place. Batch mode rebuilds the table, but it reflects the *existing*
        # constraints unless given an explicit definition — so `copy_from` is
        # required to also correct the foreign key's ON DELETE rule.
        with op.batch_alter_table(
            "transactions",
            schema=None,
            copy_from=_corrected_transactions_table(),
        ) as batch_op:
            batch_op.alter_column(
                "linked_account_id",
                existing_type=sa.String(length=36),
                nullable=True,
            )

    # --- 3. Restore the missing indexes ------------------------------------ #
    for index_name, table_name, columns in MISSING_INDEXES:
        if index_name in _existing_index_names(table_name):
            continue
        op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    for index_name, table_name, _columns in reversed(MISSING_INDEXES):
        if index_name in _existing_index_names(table_name):
            op.drop_index(index_name, table_name=table_name)

    if dialect == "postgresql":
        op.drop_constraint(_PG_FK_NAME, "transactions", type_="foreignkey")
        op.create_foreign_key(
            _PG_FK_NAME,
            "transactions",
            "linked_external_accounts",
            ["linked_account_id"],
            ["linked_account_id"],
            ondelete="RESTRICT",
        )
        op.alter_column(
            "transactions",
            "linked_account_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
    else:
        with op.batch_alter_table("transactions", schema=None) as batch_op:
            batch_op.alter_column(
                "linked_account_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )
