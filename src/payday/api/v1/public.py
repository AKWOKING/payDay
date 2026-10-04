from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from payday.core.config import settings
from payday.core.counters import get_counter_store
from payday.core.database import get_db
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
    description=(
        "Reports the state of the service and its dependencies. Kept for "
        "backwards compatibility with existing dashboards and clients. It "
        "always answers 200, so it is a status report, NOT a load-balancer or "
        "orchestrator probe — use /health/live to decide whether to restart an "
        "instance and /health/ready to decide whether it should receive "
        "traffic."
    ),
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
    "/health/live",
    response_model=APIResponse[dict],
    summary="Liveness Probe",
    description=(
        "Answers one question: is this process able to serve HTTP at all? "
        "It deliberately touches no dependency — a liveness failure means "
        "'restart this instance', and that must never be triggered by a "
        "database or Redis outage that every instance is experiencing."
    ),
)
async def liveness_probe():
    return APIResponse(
        success=True,
        message="Alive",
        data={
            "status": "UP",
            "version": settings.VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@router.get(
    "/health/ready",
    response_model=APIResponse[dict],
    summary="Readiness Probe",
    description=(
        "Answers: should this instance receive traffic? Checks the two "
        "dependencies whose absence makes money operations incorrect — the "
        "database and the shared counter store. Returns 503 when either is "
        "down so a load balancer, autoscaler or orchestrator can act on it; "
        "the previous single /health probe always answered 200 even when Redis "
        "was unreachable, which no orchestrator can act on."
    ),
    responses={503: {"description": "One or more critical dependencies are unavailable"}},
)
async def readiness_probe(
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    checks: dict[str, dict] = {}

    # Database: the ledger of record. Without it, no money operation is correct.
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = {"status": "UP"}
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        checks["database"] = {"status": "DOWN", "error": type(exc).__name__}

    # Shared counter store: login throttle and PIN lockout live here. In
    # production the process refuses to start without it (WS-0), so a DOWN
    # here means it went away after startup.
    try:
        redis_ok = await get_counter_store().ping()
        checks["counter_store"] = {
            "status": "UP" if redis_ok else "DOWN",
            "backend": settings.COUNTER_BACKEND,
        }
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        checks["counter_store"] = {"status": "DOWN", "error": type(exc).__name__}

    ready = all(check["status"] == "UP" for check in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return APIResponse(
        success=ready,
        message="Ready" if ready else "Not ready",
        data={
            "status": "READY" if ready else "NOT_READY",
            "environment": settings.ENVIRONMENT,
            "version": settings.VERSION,
            "telco_mode": settings.TELCO_MODE,
            "checks": checks,
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
