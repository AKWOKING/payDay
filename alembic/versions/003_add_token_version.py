"""003_add_token_version

Revocable sessions (WS-2 / LB-7).

Revision ID: 003_add_token_version
Revises: 002_fix_schema_drift
Create Date: 2026-09-13 00:00:00.000000

WHY THIS MIGRATION EXISTS
-------------------------
Pre-launch discovery found that refresh tokens could not be revoked:

- `AuthService.refresh_tokens` decoded the JWT, looked the user up, and issued
  a fresh pair whenever the account was `ACTIVE`. There was no denylist, no
  `jti` tracking, and no token version.
- A stolen refresh token therefore stayed valid for its full 7 days. The only
  remedy available to support was suspending the entire account.
- Logout was client-side only: the server had no concept of a session ending.
- It also made a correct password reset (LB-1) impossible. The point of a
  reset is to evict whoever compromised the account; without revocation the
  attacker keeps access for up to 7 days *after* the victim resets, while
  support tells the user they are safe.

This migration adds the counter that makes eviction possible:

    users.token_version INTEGER NOT NULL DEFAULT 0

Tokens are minted with the counter's value in a `tv` claim; `get_current_user`
and `refresh_tokens` reject a token whose `tv` is not the current value.
`AuthService.revoke_all_sessions()` increments the column, which kills every
token issued before that moment.

NOT NULL with a server-side default is deliberate: existing rows must backfill
to 0 (they have no live sessions to invalidate, and 0 keeps any token issued
before this deploy valid until the first real revocation), and a DEFAULT lets
rows be inserted without naming the column.

REVISION NUMBER
---------------
The launch-blocker roadmap planned this as "migration 004", assuming the
notification-delivery migration (WS-1 / LB-3) would land first as 003. WS-2 was
executed before WS-1, so the chain head was still `002_fix_schema_drift` and
this revision takes the next number, 003.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_add_token_version"
down_revision: Union[str, None] = "002_fix_schema_drift"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
