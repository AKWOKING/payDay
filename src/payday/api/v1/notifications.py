from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from payday.core.database import get_db
from payday.schemas.common import APIResponse
from payday.schemas.notification import NotificationListResponse, NotificationItemResponse
from payday.models.notification import NotificationChannel
from payday.models.user import User
from payday.api.deps import get_current_user
from payday.services.notification_service import notification_service

router = APIRouter(prefix="/notifications", tags=["Notifications & Alerts"])


@router.get(
    "",
    response_model=APIResponse[NotificationListResponse],
    summary="Get User Notifications",
    description="Retrieves paginated SMS and Push notification alerts dispatched to the authenticated user.",
)
async def get_notifications(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    channel: Optional[NotificationChannel] = Query(None, description="Filter by channel (SMS, PUSH)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await notification_service.get_user_notifications(
        db=db,
        user_id=current_user.user_id,
        page=page,
        page_size=page_size,
        channel=channel,
    )

    data = NotificationListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[NotificationItemResponse.model_validate(n) for n in items],
    )

    return APIResponse(
        success=True,
        message="Notifications retrieved successfully",
        data=data,
    )
