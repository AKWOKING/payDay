from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from payday.core.database import get_db
from payday.schemas.common import APIResponse
from payday.schemas.transaction import WebhookCallbackPayload
from payday.services.transaction_manager import transaction_manager
from payday.models.transaction import TransactionChannel

router = APIRouter(prefix="/mock-telco", tags=["Mock Sandbox Controls"])


@router.post(
    "/mtn/simulate-callback",
    response_model=APIResponse[dict],
    summary="Simulate MTN MoMo USSD Approval or Rejection",
    description="Development & Testing tool allowing Flutter/Angular developers to simulate customer approving USSD prompt on their handset.",
)
async def simulate_mtn_callback(
    payload: WebhookCallbackPayload,
    db: AsyncSession = Depends(get_db),
):
    tx = await transaction_manager.process_webhook(
        db=db,
        channel=TransactionChannel.MTN,
        payload=payload,
    )
    return APIResponse(
        success=True,
        message=f"Simulated MTN callback processed: Transaction {tx.status.value}",
        data={
            "transaction_id": tx.transaction_id,
            "external_ref": tx.external_ref,
            "new_status": tx.status.value,
        },
    )
