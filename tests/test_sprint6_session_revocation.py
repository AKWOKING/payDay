"""Sprint 6 — WS-2 / LB-7: revocable sessions.

Pre-launch the API issued 7-day refresh tokens that nothing could invalidate:
no denylist, no `jti` tracking, no token version. `refresh_tokens` only checked
that the user existed and was `ACTIVE`, so a stolen refresh token survived for
its full lifetime and logout was a client-side fiction. That also made a
correct password reset (LB-1) impossible — the attacker would keep minting
tokens after the victim reset their password.

The fix is a counter: `users.token_version`, carried by every token in a `tv`
claim and compared on every use. `AuthService.revoke_all_sessions()` increments
it, which evicts everything issued before the increment.

These tests exercise the invariants that make that claim true — including the
ones where a careless implementation would still look fine: an access token must
die as well as a refresh token, re-activating a suspended account must not
resurrect old tokens, and one user's logout must not sign another user out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jose.jwt as jose_jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from payday.core.config import settings
from payday.core.security import (
    create_access_token,
    create_refresh_token,
    token_version_matches,
)
from payday.models.audit_log import AuditLog
from payday.models.user import User, UserStatus
from sqlalchemy.future import select

PASSWORD = "SecretP@ssword123"


async def _login(client: AsyncClient, phone: str, password: str = PASSWORD) -> dict:
    response = await client.post(
        "/api/v1/auth/login",
        json={"phone_number": phone, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _audit_actions(db: AsyncSession, user_id: str) -> list[str]:
    result = await db.execute(
        select(AuditLog.action).where(AuditLog.entity_id == user_id)
    )
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# The four WS-2 acceptance criteria from docs/LAUNCH_BLOCKER_ROADMAP.md
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_refresh_token_issued_before_revocation_is_rejected(
    client: AsyncClient, test_user: User, db_session: AsyncSession
) -> None:
    """A refresh token captured before eviction must not mint a new pair."""
    tokens = await _login(client, test_user.phone_number)
    stolen_refresh = tokens["refresh_token"]

    # Still valid at this point — otherwise the test proves nothing.
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen_refresh})
    ).status_code == 200

    from payday.services.auth_service import auth_service

    await auth_service.revoke_all_sessions(db_session, test_user.user_id, reason="TEST")

    rejected = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": stolen_refresh}
    )
    assert rejected.status_code == 401, rejected.text
    assert rejected.json()["code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_access_token_issued_before_revocation_is_rejected(
    client: AsyncClient, test_user: User, db_session: AsyncSession
) -> None:
    """Eviction must cover the access token, not only the refresh token.

    A design that versioned refresh tokens alone would leave a stolen access
    token usable until it expired (30 minutes by default) — and would pass a
    test that only ever refreshed.
    """
    tokens = await _login(client, test_user.phone_number)
    access = tokens["access_token"]

    assert (await client.get("/api/v1/auth/me", headers=_bearer(access))).status_code == 200

    from payday.services.auth_service import auth_service

    await auth_service.revoke_all_sessions(db_session, test_user.user_id, reason="TEST")

    rejected = await client.get("/api/v1/auth/me", headers=_bearer(access))
    assert rejected.status_code == 401, rejected.text
    assert rejected.json()["code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_logout_invalidates_the_refresh_token(
    client: AsyncClient, test_user: User
) -> None:
    """`POST /auth/logout` is server-side: the token dies, not just the client state."""
    tokens = await _login(client, test_user.phone_number)
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    logged_out = await client.post("/api/v1/auth/logout", headers=_bearer(access))
    assert logged_out.status_code == 200, logged_out.text
    assert logged_out.json()["data"]["sessions_revoked"] is True

    # The refresh token used to outlive logout for its full 7 days.
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert after.status_code == 401, after.text
    assert after.json()["code"] == "SESSION_REVOKED"

    # ...and so does the access token that performed the logout.
    assert (await client.get("/api/v1/auth/me", headers=_bearer(access))).status_code == 401


@pytest.mark.asyncio
async def test_admin_suspension_immediately_invalidates_active_sessions(
    client: AsyncClient, test_user: User, test_admin: User, admin_auth_headers: dict
) -> None:
    """Suspension evicts; and re-activation must not resurrect the same tokens.

    The reactivation half is the part that matters. A status check alone already
    refuses a suspended user's requests — but it also *un-refuses* them the
    moment an admin flips the account back to ACTIVE, silently restoring every
    token the blocked user still held. Eviction is what makes suspension stick.
    """
    tokens = await _login(client, test_user.phone_number)
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    suspended = await client.post(
        f"/api/v1/admin/users/{test_user.user_id}/status",
        params={"status_val": "SUSPENDED", "reason": "Investigation"},
        headers=admin_auth_headers,
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["data"]["sessions_revoked"] is True

    assert (await client.get("/api/v1/auth/me", headers=_bearer(access))).status_code in (401, 403)
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    ).status_code in (401, 403)

    reactivated = await client.post(
        f"/api/v1/admin/users/{test_user.user_id}/status",
        params={"status_val": "ACTIVE", "reason": "Cleared"},
        headers=admin_auth_headers,
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["data"]["sessions_revoked"] is False

    # Pre-suspension credentials must stay dead after re-activation.
    after = await client.get("/api/v1/auth/me", headers=_bearer(access))
    assert after.status_code == 401, (
        "Re-activating the account restored a token issued before the suspension"
    )
    assert after.json()["code"] == "SESSION_REVOKED"
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    ).status_code == 401

    # A fresh login after reactivation must work, or the account is bricked.
    fresh = await _login(client, test_user.phone_number)
    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(fresh["access_token"]))
    ).status_code == 200


# --------------------------------------------------------------------------- #
# Blast radius and counter arithmetic
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_revocation_is_scoped_to_one_account(
    client: AsyncClient, test_user: User, db_session: AsyncSession
) -> None:
    """One user's logout must not sign other users out.

    A counter stored anywhere but per-user (a global epoch, a shared key) would
    pass every test above and evict the entire customer base on one logout.
    """
    from decimal import Decimal

    from payday.core.security import get_password_hash
    from payday.models.user import KycStatus, UserRole
    from payday.models.wallet import Wallet, WalletStatus

    other = User(
        full_name="Second Customer",
        phone_number="+237699887766",
        email="second@example.cm",
        password_hash=get_password_hash(PASSWORD),
        kyc_status=KycStatus.VERIFIED,
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        Wallet(
            user_id=other.user_id,
            balance=Decimal("1000.00"),
            locked_balance=Decimal("0.00"),
            currency="XAF",
            status=WalletStatus.ACTIVE,
            daily_limit=Decimal("500000.00"),
            monthly_limit=Decimal("5000000.00"),
        )
    )
    await db_session.commit()

    victim = await _login(client, test_user.phone_number)
    bystander = await _login(client, other.phone_number)

    from payday.services.auth_service import auth_service

    await auth_service.revoke_all_sessions(db_session, test_user.user_id, reason="TEST")

    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(victim["access_token"]))
    ).status_code == 401
    unaffected = await client.get(
        "/api/v1/auth/me", headers=_bearer(bystander["access_token"])
    )
    assert unaffected.status_code == 200, "Another account's session was revoked"


@pytest.mark.asyncio
async def test_repeated_revocations_each_increment_the_version(
    client: AsyncClient, test_user: User, db_session: AsyncSession
) -> None:
    """The counter advances on every revocation and earlier tokens stay dead.

    Guards against an implementation that *sets* the version rather than
    incrementing it, which would let a token minted between two revocations
    survive the second one.
    """
    from payday.services.auth_service import auth_service

    first = await _login(client, test_user.phone_number)
    v1 = await auth_service.revoke_all_sessions(db_session, test_user.user_id, reason="TEST")
    second = await _login(client, test_user.phone_number)
    v2 = await auth_service.revoke_all_sessions(db_session, test_user.user_id, reason="TEST")

    assert v2 == v1 + 1, f"token_version went {v1} -> {v2} instead of incrementing"
    for tokens in (first, second):
        assert (
            await client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
        ).status_code == 401


@pytest.mark.asyncio
async def test_revocation_is_audited_with_its_reason(
    client: AsyncClient, test_user: User, db_session: AsyncSession
) -> None:
    """Eviction is a security event; it must be attributable after the fact."""
    tokens = await _login(client, test_user.phone_number)
    await client.post("/api/v1/auth/logout", headers=_bearer(tokens["access_token"]))

    assert "SESSIONS_REVOKED" in await _audit_actions(db_session, test_user.user_id)


@pytest.mark.asyncio
async def test_new_tokens_after_logout_are_versioned_above_the_old_ones(
    client: AsyncClient, test_user: User
) -> None:
    """Logging back in must mint tokens at the *new* version, not the old one.

    The obvious way to ship revocation and break the product: evict everything
    and then issue replacements that are already stale, so the user cannot log
    back in at all.
    """
    before = await _login(client, test_user.phone_number)
    await client.post("/api/v1/auth/logout", headers=_bearer(before["access_token"]))

    after = await _login(client, test_user.phone_number)
    claims_before = jose_jwt.get_unverified_claims(before["access_token"])
    claims_after = jose_jwt.get_unverified_claims(after["access_token"])

    assert claims_after["tv"] > claims_before["tv"]
    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(after["access_token"]))
    ).status_code == 200
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": after["refresh_token"]})
    ).status_code == 200


# --------------------------------------------------------------------------- #
# Upgrade path: tokens minted before this feature existed
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_legacy_token_without_tv_claim_works_until_the_first_revocation(
    client: AsyncClient, test_user: User, db_session: AsyncSession
) -> None:
    """Pre-deploy tokens must keep working, then die at the first revocation.

    Rejecting them outright would log out every user the moment this ships.
    Accepting them forever would be a permanent revocation bypass for anyone who
    captured a token before the upgrade — the assertion below is the one that
    matters.
    """
    legacy = jose_jwt.encode(
        {
            "sub": test_user.user_id,
            "role": test_user.role.value,
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(legacy))
    ).status_code == 200, "A token minted before the upgrade was rejected on deploy"

    from payday.services.auth_service import auth_service

    await auth_service.revoke_all_sessions(db_session, test_user.user_id, reason="TEST")

    assert (
        await client.get("/api/v1/auth/me", headers=_bearer(legacy))
    ).status_code == 401, "A pre-upgrade token survived revocation"


def test_token_version_matches_rejects_non_numeric_claims() -> None:
    """A `tv` that cannot be compared to an integer is a mismatch, not a 500."""
    assert token_version_matches({"tv": 3}, 3) is True
    assert token_version_matches({"tv": 3}, 4) is False
    assert token_version_matches({}, 0) is True           # legacy token
    assert token_version_matches({}, 1) is False          # legacy, post-revocation
    assert token_version_matches({"tv": "not-a-number"}, 0) is False
    assert token_version_matches({"tv": None}, 0) is False


@pytest.mark.asyncio
async def test_minted_tokens_carry_the_users_current_version(test_user: User) -> None:
    """Both token types embed the account's live `token_version`."""
    refresh = create_refresh_token(
        subject=test_user.user_id, role=test_user.role.value, token_version=7
    )
    access = create_access_token(
        subject=test_user.user_id, role=test_user.role.value, token_version=7
    )

    assert token_version_matches(jose_jwt.get_unverified_claims(refresh), 7)
    assert token_version_matches(jose_jwt.get_unverified_claims(access), 7)
    assert not token_version_matches(jose_jwt.get_unverified_claims(access), 8)


# --------------------------------------------------------------------------- #
# Migration 003 — the counter must exist on a *migrated* database
# --------------------------------------------------------------------------- #
# The rest of this file, like the rest of the suite, runs against a schema built
# from the models by `Base.metadata.create_all()`. Production is provisioned by
# `alembic upgrade head`, which is a different code path — the one that shipped
# the 001 drift. So the column is asserted on a migrated database too.
#
# Synchronous by necessity: `alembic/env.py` calls `asyncio.run()` and raises if
# a loop is already running.
def test_migration_003_adds_a_backfilled_not_null_token_version(tmp_path) -> None:
    import os
    from pathlib import Path

    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "token_version.db"
    async_url = f"sqlite+aiosqlite:///{db_path}"

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", async_url)
    try:
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        inspector = sa.inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("users")}
        assert "token_version" in columns, "migration 003 did not add users.token_version"

        column = columns["token_version"]
        assert column["nullable"] is False, "token_version must be NOT NULL"
        # The server default is what backfills accounts that already exist —
        # without it, `upgrade head` fails on a populated users table.
        assert column["default"] is not None, "token_version has no server default"

        # A row inserted without naming the column must come out at 0.
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO users (user_id, full_name, phone_number, email, "
                    "password_hash, kyc_status, role, status, created_at, updated_at) "
                    "VALUES ('probe-1', 'Probe', '+237600000001', NULL, 'x', "
                    "'PENDING', 'CUSTOMER', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            version = conn.execute(
                sa.text("SELECT token_version FROM users WHERE user_id = 'probe-1'")
            ).scalar_one()
        assert version == 0, f"token_version backfilled to {version!r}, expected 0"
    finally:
        engine.dispose()
