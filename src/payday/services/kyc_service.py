from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from payday.models.user import User, KycStatus
from payday.core.encryption import encryption_service
from payday.core.exceptions import UserNotFoundError, PayDayException
from payday.services.audit_service import audit_service


class KycService:
    @staticmethod
    async def submit_kyc(
        db: AsyncSession,
        user_id: str,
        id_document_no: str,
        id_document_type: str = "NATIONAL_ID",
    ) -> User:
        result = await db.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()
        if not user:
            raise UserNotFoundError()

        old_state = {"kyc_status": user.kyc_status.value, "id_document_type": user.id_document_type}
        
        # Encrypt the ID document number using AES-256-GCM
        encrypted_id = encryption_service.encrypt(id_document_no)
        user.id_document_no_encrypted = encrypted_id
        user.id_document_type = id_document_type
        user.kyc_status = KycStatus.PENDING

        await audit_service.log_action(
            db=db,
            action="KYC_SUBMITTED",
            entity_name="User",
            entity_id=user_id,
            actor_id=user_id,
            old_state=old_state,
            new_state={"kyc_status": KycStatus.PENDING.value, "id_document_type": id_document_type},
        )
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def get_kyc_status(db: AsyncSession, user_id: str) -> dict:
        result = await db.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()
        if not user:
            raise UserNotFoundError()

        masked_doc = "UNAVAILABLE"
        if user.id_document_no_encrypted:
            try:
                decrypted = encryption_service.decrypt(user.id_document_no_encrypted)
                if len(decrypted) > 4:
                    masked_doc = "*" * (len(decrypted) - 4) + decrypted[-4:]
                else:
                    masked_doc = "****"
            except Exception:
                masked_doc = "ENCRYPTED"

        return {
            "user_id": user.user_id,
            "kyc_status": user.kyc_status,
            "id_document_type": user.id_document_type,
            "id_document_masked": masked_doc,
            "verified_at": user.updated_at if user.kyc_status == KycStatus.VERIFIED else None,
        }

    @staticmethod
    async def review_kyc(
        db: AsyncSession,
        admin_id: str,
        user_id: str,
        status: KycStatus,
        reason: Optional[str] = None,
    ) -> User:
        result = await db.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()
        if not user:
            raise UserNotFoundError()

        old_state = {"kyc_status": user.kyc_status.value}
        user.kyc_status = status

        await audit_service.log_action(
            db=db,
            action=f"KYC_{status.value}",
            entity_name="User",
            entity_id=user_id,
            actor_id=admin_id,
            old_state=old_state,
            new_state={"kyc_status": status.value, "reason": reason},
        )
        await db.commit()
        await db.refresh(user)
        return user


kyc_service = KycService()
