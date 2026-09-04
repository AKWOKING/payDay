from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field
from payday.models.user import KycStatus


class KycSubmitRequest(BaseModel):
    id_document_no: str = Field(..., min_length=5, max_length=50, examples=["119283746"])
    id_document_type: str = Field(default="NATIONAL_ID", examples=["NATIONAL_ID", "PASSPORT", "RESIDENCE_PERMIT"])


class KycStatusResponse(BaseModel):
    user_id: str
    kyc_status: KycStatus
    id_document_type: str
    id_document_masked: str
    verified_at: Optional[datetime] = None


class KycReviewRequest(BaseModel):
    status: KycStatus = Field(..., description="VERIFIED or REJECTED")
    rejection_reason: Optional[str] = None
