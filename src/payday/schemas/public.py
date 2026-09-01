from decimal import Decimal
from typing import Dict, Any
from pydantic import BaseModel, Field
from payday.models.linked_account import ChannelProvider
from payday.models.transaction import TransactionType


class FeeCalculatorRequest(BaseModel):
    type: TransactionType = Field(..., examples=["DEPOSIT", "WITHDRAW"])
    channel: ChannelProvider = Field(..., examples=["MTN", "ORANGE", "UBA"])
    amount: Decimal = Field(..., gt=0, examples=[25000.00])


class FeeCalculatorResponse(BaseModel):
    amount: Decimal
    fee: Decimal
    total_charged: Decimal
    net_credited: Decimal
    currency: str = "XAF"
    fee_percentage: float


class PublicStatusResponse(BaseModel):
    service: str
    status: str
    version: str
    active_channels: Dict[str, str]
    system_time: str
