from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter
from payday.core.config import settings
from payday.core.counters import get_counter_store
from payday.schemas.common import APIResponse
from payday.schemas.public import (
    FeeCalculatorRequest,
    FeeCalculatorResponse,
    PublicStatusResponse,
)
from payday.services.wallet_engine import wallet_engine
from payday.models.transaction import TransactionType

router = APIRouter(prefix="/public", tags=["Public & Landing Page"])


def _channel_state() -> str:
    """How the mobile-money channels are actually running.

    ACTIVE   — live operator APIs, real money
    SANDBOX  — real operator APIs, operator sandbox
    SIMULATED— in-process simulator; no operator is contacted
    """
    mode = settings.TELCO_MODE
    if mode == "live":
        return "ACTIVE"
    if mode == "sandbox":
        return "SANDBOX"
    return "SIMULATED"


@router.get(
    "/health",
    response_model=APIResponse[dict],
    summary="Health & Liveness Probe",
    description="Returns backend server health and timestamp.",
)
async def health_check():
    redis_ok = await get_counter_store().ping()
    return APIResponse(
        success=True,
        message="PayDay Core Service Healthy",
        data={
            "status": "UP" if redis_ok else "DEGRADED",
            "environment": settings.ENVIRONMENT,
            "version": settings.VERSION,
            "counter_backend": settings.COUNTER_BACKEND,
            "redis": {
                "status": "UP" if redis_ok else "DOWN",
                "backend": settings.COUNTER_BACKEND,
            },
            # Which payment-channel implementation is actually serving traffic.
            # "mock" means the in-process simulator: no money moves and no
            # operator is contacted, so this must be visible to operators and
            # to anyone reading a health dashboard.
            "telco_mode": settings.TELCO_MODE,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@router.get(
    "/info",
    response_model=APIResponse[PublicStatusResponse],
    summary="Public System Information for Landing Page",
    description="Provides active channel states and system information for the Angular marketing showcase.",
)
async def system_info():
    return APIResponse(
        success=True,
        data=PublicStatusResponse(
            service=settings.PROJECT_NAME,
            status="OPERATIONAL",
            version=settings.VERSION,
            active_channels={
                # These values state what the channel actually is, per configured
                # mode. They previously read "ACTIVE" unconditionally, which was
                # false whenever the mock adapter was serving traffic (M1 / LB-8).
                "MTN_MOMO": _channel_state(),
                "ORANGE_MONEY": _channel_state(),
                "UBA_BANK": "PLANNED_V2",
            },
            system_time=datetime.now(timezone.utc).isoformat(),
        ),
    )


@router.post(
    "/fee-calculator",
    response_model=APIResponse[FeeCalculatorResponse],
    summary="Public Fee & Cost Calculator",
    description="Calculates transparent platform fees for deposit or withdrawal, used on the public Angular landing demo.",
)
async def calculate_fee(req: FeeCalculatorRequest):
    fee = wallet_engine.calculate_fee(req.type, req.amount)
    
    if req.type == TransactionType.DEPOSIT:
        total_charged = req.amount
        net_credited = req.amount - fee
        fee_pct = settings.DEFAULT_DEPOSIT_FEE_PERCENTAGE * 100
    else:
        total_charged = req.amount + fee
        net_credited = req.amount
        fee_pct = settings.DEFAULT_WITHDRAW_FEE_PERCENTAGE * 100

    return APIResponse(
        success=True,
        data=FeeCalculatorResponse(
            amount=req.amount,
            fee=fee,
            total_charged=total_charged,
            net_credited=net_credited,
            currency=settings.DEFAULT_CURRENCY,
            fee_percentage=fee_pct,
        ),
    )
