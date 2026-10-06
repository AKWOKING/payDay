"""The simulator settles without verifying anything, so it must not reach real money.

`/api/v1/mock-telco/*/simulate-callback` exists so the frontend can drive a
deposit to completion while `TELCO_MODE=mock`. Those endpoints call
`transaction_manager.process_webhook`, which — unlike the operator webhook path
(`api/v1/webhooks.py` → `_settle_from_callback`) — does **no** signature
verification and **no** operator re-query. It goes straight to
`_apply_settlement`, which credits a deposit or finalises a withdrawal.

Before this lockdown the routes were mounted unconditionally, were
unauthenticated, and were reachable in a live deployment. A deposit's
`external_ref` is returned to its own creator, so the attack needed no guessing:
initiate a deposit, never approve the USSD prompt, then POST the simulator with
the transaction's own reference and `status: SUCCESSFUL` — balance created from
nothing and withdrawable. This file is the regression net for that.

Two locks are asserted here, deliberately independent of one another:

1. **Mounting** — `simulator_is_mounted()` is false outside mock mode, and
   `build_api_router()` therefore contains no simulator paths.
2. **Behaviour** — `process_webhook` refuses in live mode even when called
   directly, so a future route cannot silently re-open the first lock.

Plus the sibling finding from the same audit: a production deployment must not
start with the repository's published development secrets.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payday.api.v1.router import build_api_router, simulator_is_mounted
from payday.core.config import (
    DEVELOPMENT_ENCRYPTION_KEY,
    DEVELOPMENT_SECRET_KEY,
    SecurityConfigurationError,
    settings,
    validate_security_configuration,
)
from payday.core.exceptions import PayDayException
from payday.models.transaction import (
    Transaction,
    TransactionChannel,
    TransactionStatus,
    TransactionType,
)
from payday.models.wallet import Wallet
from payday.models.user import User
from payday.schemas.transaction import WebhookCallbackPayload
from payday.services.transaction_manager import transaction_manager


def _paths(router) -> set[str]:
    """The paths a router actually exposes.

    `APIRouter.routes` keeps included routers as opaque nodes in this FastAPI
    version, so the routes are resolved by mounting the router on a throwaway
    app and reading its OpenAPI document — the same flattened view a client sees.
    """
    probe = FastAPI()
    probe.include_router(router, prefix=settings.API_V1_STR)
    return set(probe.openapi()["paths"])


# ---------------------------------------------------------------------------
# Lock 1 — the simulator is not mounted unless it is the payment channel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["sandbox", "live"])
def test_simulator_is_not_mounted_outside_mock_mode(monkeypatch, mode: str):
    monkeypatch.setattr(settings, "TELCO_MODE", mode)

    assert simulator_is_mounted() is False
    mounted = _paths(build_api_router())
    assert not any("mock-telco" in path for path in mounted), (
        "The simulator settles without verification and must not be reachable "
        f"in TELCO_MODE={mode}"
    )


def test_simulator_is_mounted_in_mock_mode(monkeypatch):
    """The frontend's dev flow must keep working — this is gating, not removal."""
    monkeypatch.setattr(settings, "TELCO_MODE", "mock")

    assert simulator_is_mounted() is True
    mounted = _paths(build_api_router())
    assert any("mock-telco" in path for path in mounted)


def test_a_fully_configured_live_deployment_exposes_no_simulator_paths(monkeypatch):
    """The live case that matters: real credentials present, real money flowing."""
    monkeypatch.setattr(settings, "TELCO_MODE", "live")
    monkeypatch.setattr(settings, "MTN_COLLECTION_API_USER", "u")
    monkeypatch.setattr(settings, "ORANGE_MERCHANT_KEY", "k")

    assert not any("mock-telco" in path for path in _paths(build_api_router()))


# ---------------------------------------------------------------------------
# Lock 2 — the settlement helper itself refuses, whatever calls it
# ---------------------------------------------------------------------------


async def _processing_deposit(db_session: AsyncSession) -> Transaction:
    wallet = (await db_session.execute(select(Wallet))).scalars().first()
    tx = Transaction(
        idempotency_key="sim-lockdown-key",
        wallet_id=wallet.wallet_id,
        type=TransactionType.DEPOSIT,
        channel=TransactionChannel.MTN,
        amount=Decimal("50000.00"),
        fee=Decimal("0.00"),
        net_amount=Decimal("50000.00"),
        status=TransactionStatus.PROCESSING,
        external_ref="MTN-FORGED-REFERENCE",
    )
    db_session.add(tx)
    await db_session.commit()
    return tx


async def test_process_webhook_refuses_in_live_mode(
    db_session: AsyncSession, test_user: User, monkeypatch
):
    """A forged callback cannot move the ledger once live."""
    taken_at = datetime.now(timezone.utc).timestamp()
    assert taken_at > 0  # keep the import meaningful for readers; see assertion below

    tx = await _processing_deposit(db_session)
    monkeypatch.setattr(settings, "TELCO_MODE", "live")

    with pytest.raises(PayDayException) as exc:
        await transaction_manager.process_webhook(
            db=db_session,
            channel=TransactionChannel.MTN,
            payload=WebhookCallbackPayload(
                external_ref=tx.external_ref, status="SUCCESSFUL"
            ),
        )

    assert exc.value.code == "SIMULATOR_DISABLED"
    assert exc.value.status_code == 403

    # The money must not have moved, and the row must not have been touched.
    await db_session.refresh(tx)
    assert tx.status == TransactionStatus.PROCESSING
    assert tx.completed_at is None


async def test_process_webhook_still_settles_in_mock_mode(
    db_session: AsyncSession, test_user: User, monkeypatch
):
    """The lockdown must not break the simulator it exists to protect."""
    tx = await _processing_deposit(db_session)
    monkeypatch.setattr(settings, "TELCO_MODE", "mock")

    settled = await transaction_manager.process_webhook(
        db=db_session,
        channel=TransactionChannel.MTN,
        payload=WebhookCallbackPayload(external_ref=tx.external_ref, status="SUCCESSFUL"),
    )

    assert settled.status == TransactionStatus.SUCCESS


# ---------------------------------------------------------------------------
# The same audit's sibling finding: production must not run on published secrets
# ---------------------------------------------------------------------------


def test_production_refuses_the_published_development_secrets():
    production = settings.model_copy(update={"ENVIRONMENT": "production"})

    with pytest.raises(SecurityConfigurationError) as exc:
        validate_security_configuration(production)

    message = str(exc.value)
    assert "SECRET_KEY" in message
    assert "ENCRYPTION_KEY" in message


def test_production_accepts_generated_secrets():
    production = settings.model_copy(
        update={
            "ENVIRONMENT": "production",
            "SECRET_KEY": "a" * 64,
            "ENCRYPTION_KEY": "b" * 64,
            "DEBUG": False,
        }
    )

    assert validate_security_configuration(production) == []


def test_production_rejects_a_short_secret():
    """Length matters independently of the default: HS256 material must not be
    brute-forceable offline from one captured token."""
    production = settings.model_copy(
        update={"ENVIRONMENT": "production", "SECRET_KEY": "short-but-not-default"}
    )

    with pytest.raises(SecurityConfigurationError) as exc:
        validate_security_configuration(production)

    assert "shorter than" in str(exc.value)


def test_development_is_never_blocked():
    """Local development and CI run on the defaults by design."""
    assert validate_security_configuration(settings) == []
    assert settings.SECRET_KEY == DEVELOPMENT_SECRET_KEY
    assert settings.ENCRYPTION_KEY == DEVELOPMENT_ENCRYPTION_KEY
