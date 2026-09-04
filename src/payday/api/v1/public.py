from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter
from payday.core.config import settings
from payday.schemas.common import APIResponse
from payday.schemas.public import (
    FeeCalculatorRequest,
    FeeCalculatorResponse,
    PublicStatusResponse,
)
from payday.services.wallet_engine import wallet_engine
from payday.models.transaction import TransactionType

router = APIRouter(prefix="/public", tags=["Public & Landing Page"])


@router.get(
    "/health",
    response_model=APIResponse[dict],
    summary="Health & Liveness Probe",
    description="Returns backend server health and timestamp.",
)
async def health_check():
    return APIResponse(
        success=True,
        message="PayDay Core Service Healthy",
        data={
            "status": "UP",
            "environment": settings.ENVIRONMENT,
            "version": settings.VERSION,
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
                "MTN_MOMO": "ACTIVE",
                "ORANGE_MONEY": "ACTIVE",
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
