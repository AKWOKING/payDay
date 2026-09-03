from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict
from payday.models.notification import NotificationChannel, NotificationStatus


class NotificationItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_id: str
    user_id: str
    transaction_id: Optional[str] = None
    channel: NotificationChannel
    recipient: str
    message: str
    status: NotificationStatus
    sent_at: Optional[datetime] = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[NotificationItemResponse]
