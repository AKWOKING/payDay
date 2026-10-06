"""004_provider_callback_refs

Operator callback verification (M1 / LB-12, LB-13).

Revision ID: 004_provider_callback_refs
Revises: 003_add_token_version
Create Date: 2026-09-13 00:00:00.000000

WHY THIS MIGRATION EXISTS
-------------------------
Settling a mobile-money transaction correctly requires matching an operator's
callback to one of our transactions and then confirming the outcome with the
operator's own status API. Neither was possible with the columns that existed:

1. **Orange callbacks could not be matched at all.** Orange's notification body
   is `{"status", "notif_token", "txnid"}` — three fields, with no order id, no
   amount and no reference. The only identifier that ties it to an order is
   `notif_token`, which Orange returns in the *initiation* response and which
   the adapter then discarded. `provider_notif_token` stores it, and is indexed
   because it is the callback lookup key.

2. **Orange's status API could not be called.** The documented contract is
   `POST /transactionstatus` with `{order_id, amount, pay_token}`. The code
   stored `pay_token` (as `Transaction.external_ref`) but nothing kept
   `order_id`, so the requery had no valid input. `provider_order_id` stores it.

3. **Support and reconciliation had no operator-side identifier.** When a
   customer disputes a payment, MTN's `financialTransactionId` / Orange's
   `txnid` is what their call centre can search on. `provider_txn_id` stores it.

All three are nullable: they are written when an initiation creates them, and
rows that predate live mode have no value to backfill. No default is safe here —
a fabricated `notif_token` would make a forged callback look verifiable.

REVISION NUMBER
---------------
The MVP roadmap allocated 004 to WS-1 (notification delivery) and 005 to WS-6
(KYC). Neither migration existed yet, so this revision takes 004 and those two
renumber to 005 and 006 when they land (recorded in the roadmap as R21).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_provider_callback_refs"
down_revision: Union[str, None] = "003_add_token_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("provider_order_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("provider_notif_token", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("provider_txn_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_transactions_provider_notif_token",
        "transactions",
        ["provider_notif_token"],
    )
    op.create_index(
        "ix_transactions_provider_txn_id",
        "transactions",
        ["provider_txn_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_provider_txn_id", table_name="transactions")
    op.drop_index("ix_transactions_provider_notif_token", table_name="transactions")
    op.drop_column("transactions", "provider_txn_id")
    op.drop_column("transactions", "provider_notif_token")
    op.drop_column("transactions", "provider_order_id")
