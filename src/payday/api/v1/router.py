from fastapi import APIRouter

from payday.core.config import settings
from payday.api.v1.auth import router as auth_router
from payday.api.v1.kyc import router as kyc_router
from payday.api.v1.wallet import router as wallet_router
from payday.api.v1.transactions import router as transactions_router
from payday.api.v1.notifications import router as notifications_router
from payday.api.v1.webhooks import router as webhooks_router
from payday.api.v1.mock_telco import router as mock_telco_router
from payday.api.v1.admin import router as admin_router
from payday.api.v1.public import router as public_router


def simulator_is_mounted(mode: str | None = None) -> bool:
    """Whether the mock telco simulator belongs in this deployment.

    The simulator endpoints (`/mock-telco/*/simulate-callback`) move the ledger
    through `transaction_manager.process_webhook`, which — unlike the operator
    webhook path — performs **no signature verification and no operator
    re-query**. That is correct for a simulator and fatal for anything else:
    exposed in a real deployment it is an unauthenticated way to mark a
    transaction SUCCESS, i.e. to mint balance without a payment.

    So it is mounted only when the simulator *is* the payment channel
    (`TELCO_MODE=mock`). Sandbox mode deliberately does not get it either: A5
    exists to verify our integration against the operators' sandboxes, and a
    local endpoint that settles without the operator would quietly invalidate
    that evidence.
    """
    return (mode or settings.TELCO_MODE) == "mock"


def build_api_router() -> APIRouter:
    """Assemble the v1 router set for the current configuration."""
    router = APIRouter()
    router.include_router(public_router)
    router.include_router(auth_router)
    router.include_router(kyc_router)
    router.include_router(wallet_router)
    router.include_router(transactions_router)
    router.include_router(notifications_router)
    router.include_router(webhooks_router)
    if simulator_is_mounted():
        router.include_router(mock_telco_router)
    router.include_router(admin_router)
    return router


api_router = build_api_router()
