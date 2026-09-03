from fastapi import APIRouter, Depends, Request, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
from payday.core.database import get_db
from payday.schemas.common import APIResponse
from payday.schemas.transaction import WebhookCallbackPayload
from payday.services.transaction_manager import transaction_manager
from payday.adapters.factory import adapter_factory
from payday.models.transaction import TransactionChannel
from payday.core.logging import logger

router = APIRouter(prefix="/webhooks", tags=["Telco Webhooks"])


@router.post(
    "/mtn",
    response_model=APIResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="MTN MoMo Asynchronous Callback Listener",
    description="Endpoint consumed by MTN MoMo gateway to notify PayDay of collection or disbursement outcome.",
)
async def mtn_webhook(
    payload: WebhookCallbackPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    headers = dict(request.headers)
    body = await request.body()

    # Verify webhook authenticity
    adapter = adapter_factory.get_adapter("MTN")
    is_valid = await adapter.verify_webhook_signature(headers, body)
    if not is_valid:
        logger.warning("[WEBHOOK] Unauthorized MTN webhook callback signature")
        return APIResponse(success=False, message="Invalid webhook signature")

    tx = await transaction_manager.process_webhook(
        db=db,
        channel=TransactionChannel.MTN,
        payload=payload,
    )

    return APIResponse(
        success=True,
        message=f"MTN Webhook processed: Transaction status {tx.status.value}",
        data={"transaction_id": tx.transaction_id, "status": tx.status.value},
    )


@router.post(
    "/orange",
    response_model=APIResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="Orange Money IPN Callback Listener",
    description="Endpoint consumed by Orange Money gateway to notify PayDay of web payment or payout outcome.",
)
async def orange_webhook(
    payload: WebhookCallbackPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    headers = dict(request.headers)
    body = await request.body()

    adapter = adapter_factory.get_adapter("ORANGE")
    is_valid = await adapter.verify_webhook_signature(headers, body)
    if not is_valid:
        logger.warning("[WEBHOOK] Unauthorized Orange webhook callback signature")
        return APIResponse(success=False, message="Invalid webhook signature")

    tx = await transaction_manager.process_webhook(
        db=db,
        channel=TransactionChannel.ORANGE,
        payload=payload,
    )

    return APIResponse(
        success=True,
        message=f"Orange Webhook processed: Transaction status {tx.status.value}",
        data={"transaction_id": tx.transaction_id, "status": tx.status.value},
    )
