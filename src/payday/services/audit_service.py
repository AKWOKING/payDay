from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from payday.models.audit_log import AuditLog
from payday.core.logging import logger


class AuditService:
    @staticmethod
    async def log_action(
        db: AsyncSession,
        action: str,
        entity_name: str,
        entity_id: str,
        actor_id: Optional[str] = None,
        old_state: Optional[Dict[str, Any]] = None,
        new_state: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        log_entry = AuditLog(
            actor_id=actor_id,
            action=action,
            entity_name=entity_name,
            entity_id=entity_id,
            old_state=old_state,
            new_state=new_state,
            ip_address=ip_address,
        )
        db.add(log_entry)
        logger.info(f"[AUDIT] Action: {action} | Entity: {entity_name}:{entity_id} | Actor: {actor_id}")
        return log_entry


audit_service = AuditService()
