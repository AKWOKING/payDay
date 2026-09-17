"""005_transfers_and_direction

Internal (PayDay → PayDay) transfers: direction, grouping and counterparty.

Revision ID: 005_transfers_and_direction
Revises: 004_provider_callback_refs
Create Date: 2026-09-17 00:00:00.000000

WHY THIS MIGRATION EXISTS
-------------------------
Money could only enter or leave the platform (deposit, withdrawal). Moving money
*between* two PayDay wallets needs three things the schema could not express:

1. **`direction`** — two legs of one internal transfer are both `TRANSFER`, and
   `amount` is constrained positive, so the sign of a movement is no longer
   derivable from `type` alone. Existing rows are backfilled from the type they
   already have (DEPOSIT credits, WITHDRAW debits).

2. **`transfer_group_id`** — both legs share one id. That turns the ledger
   invariant we care about most, "no money is created or destroyed", from a
   claim into a query: the legs of a group must sum to zero. It is indexed
   because that check runs over history, not just over new rows.

3. **`counterparty_wallet_id` / `counterparty_msisdn_masked`** — so a statement
   can say "to +2376•••233" without a join on every row and without ever putting
   a full phone number in a list response.

ENUM LABELS
-----------
`transactiontype` and `transactionchannel` are **native PostgreSQL enum types**
(SQLAlchemy renders them as `transactiontype` / `transactionchannel`; verified by
compiling the DDL for the postgresql dialect). Adding `TRANSFER` and `PAYDAY`
therefore needs `ALTER TYPE ... ADD VALUE`, which:

* must run outside a transaction on PostgreSQL (hence `autocommit_block()`), and
* cannot be undone — `downgrade()` drops the columns but leaves the labels, as
  PostgreSQL cannot remove an enum value. This is recorded rather than hidden.

On SQLite the columns are plain VARCHAR, so no label work is needed; the tests
exercise the SQLite path and the parity test confirms the end state matches the
models.

SERVER DEFAULT ON `direction`
-----------------------------
The column is NOT NULL and is added with `server_default='DEBIT'` purely so the
existing rows can be written in one step on both dialects. The real value always
comes from the ORM (`Transaction.derive_direction`, which raises for a TRANSFER
leg that does not state its side), so the server default never applies to an
application write — only to raw SQL.

REVISION NUMBER
---------------
The roadmap had assigned 005 to WS-1 (notification delivery) and 006 to WS-6
(KYC); neither existed, so this revision takes 005 and they move to 006 and 007
(recorded as R21 in `docs/MVP_EXECUTION_ROADMAP.md`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_transfers_and_direction"
down_revision: Union[str, None] = "004_provider_callback_refs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIRECTION_ENUM = sa.Enum("CREDIT", "DEBIT", name="transactiondirection")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE transactiontype ADD VALUE IF NOT EXISTS 'TRANSFER'")
            op.execute("ALTER TYPE transactionchannel ADD VALUE IF NOT EXISTS 'PAYDAY'")

    op.add_column(
        "transactions",
        sa.Column("direction", DIRECTION_ENUM, nullable=False, server_default="DEBIT"),
    )
    op.add_column(
        "transactions",
        sa.Column("transfer_group_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("counterparty_wallet_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("counterparty_msisdn_masked", sa.String(length=20), nullable=True),
    )

    # Backfill the rows that already exist: they are all deposits or withdrawals,
    # so their direction follows from their type.
    op.execute(
        "UPDATE transactions SET direction = 'CREDIT' WHERE type = 'DEPOSIT'"
    )
    op.execute(
        "UPDATE transactions SET direction = 'DEBIT' WHERE type = 'WITHDRAW'"
    )

    op.create_index(
        "ix_transactions_transfer_group_id", "transactions", ["transfer_group_id"]
    )
    op.create_index(
        "ix_transactions_counterparty_wallet_id",
        "transactions",
        ["counterparty_wallet_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_counterparty_wallet_id", table_name="transactions")
    op.drop_index("ix_transactions_transfer_group_id", table_name="transactions")
    op.drop_column("transactions", "counterparty_msisdn_masked")
    op.drop_column("transactions", "counterparty_wallet_id")
    op.drop_column("transactions", "transfer_group_id")
    op.drop_column("transactions", "direction")
    # NOTE: the TRANSFER and PAYDAY enum labels cannot be removed on PostgreSQL.
    # Downgrading therefore leaves an unused label behind, which is harmless.
