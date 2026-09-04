from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from payday.core.database import get_db
from payday.schemas.common import APIResponse
from payday.schemas.kyc import KycSubmitRequest, KycStatusResponse, KycReviewRequest
from payday.services.kyc_service import kyc_service
from payday.api.deps import get_current_user, require_roles
from payday.models.user import User, UserRole

router = APIRouter(prefix="/kyc", tags=["KYC & Compliance"])


@router.post(
    "/submit",
    response_model=APIResponse[dict],
    summary="Submit KYC Verification Document",
    description="Submits ID document number (National ID / Passport) which is encrypted using AES-256-GCM at rest.",
)
async def submit_kyc(
    req: KycSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await kyc_service.submit_kyc(
        db=db,
        user_id=current_user.user_id,
        id_document_no=req.id_document_no,
        id_document_type=req.id_document_type,
    )
    return APIResponse(
        success=True,
        message="KYC documents submitted successfully. Verification is pending review.",
        data={"user_id": current_user.user_id, "kyc_status": "PENDING"},
    )


@router.get(
    "/status",
    response_model=APIResponse[KycStatusResponse],
    summary="Check KYC Verification Status",
    description="Retrieves current KYC verification level and masked document number.",
)
async def get_kyc_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    status_data = await kyc_service.get_kyc_status(db, current_user.user_id)
    return APIResponse(success=True, data=status_data)


@router.post(
    "/review/{user_id}",
    response_model=APIResponse[dict],
    summary="Review & Verify KYC (Admin Only)",
    description="Allows compliance officers or administrators to approve (VERIFIED) or reject (REJECTED) user KYC submissions.",
)
async def review_kyc(
    user_id: str,
    req: KycReviewRequest,
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    user = await kyc_service.review_kyc(
        db=db,
        admin_id=current_admin.user_id,
        user_id=user_id,
        status=req.status,
        reason=req.rejection_reason,
    )
    return APIResponse(
        success=True,
        message=f"KYC status updated to {req.status.value}",
        data={"user_id": user.user_id, "kyc_status": user.kyc_status.value},
    )
