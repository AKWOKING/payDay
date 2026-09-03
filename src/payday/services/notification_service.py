from datetime import datetime, timezone
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from payday.models.notification import Notification, NotificationChannel, NotificationStatus
from payday.models.transaction import Transaction, TransactionType, TransactionStatus
from payday.models.user import User
from payday.core.logging import logger


class NotificationService:
    """
    Manages generation, queuing, and delivery of transactional SMS & Push alerts.
    Dispatches alerts on deposits, withdrawals, failures, and KYC status changes.
    """

    @staticmethod
    async def dispatch_transaction_alert(
        db: AsyncSession,
        user: User,
        transaction: Transaction,
        current_balance: Optional[float] = None,
    ) -> List[Notification]:
        """Dispatches transactional notifications (SMS & PUSH) on state updates."""
        notifications: List[Notification] = []

        tx_type = transaction.type.value
        tx_channel = transaction.channel.value
        amount_fmt = f"{float(transaction.amount):,.2f}"
        net_fmt = f"{float(transaction.net_amount):,.2f}"

        if transaction.status == TransactionStatus.SUCCESS:
            if transaction.type == TransactionType.DEPOSIT:
                bal_msg = f" New balance: {current_balance:,.2f} XAF." if current_balance is not None else ""
                msg = f"PayDay: Your deposit of {amount_fmt} XAF via {tx_channel} succeeded (Net credited: {net_fmt} XAF).{bal_msg} Ref: {transaction.external_ref or transaction.transaction_id[:8]}"
            else:
                msg = f"PayDay: Your withdrawal of {amount_fmt} XAF via {tx_channel} was successfully transferred. Ref: {transaction.external_ref or transaction.transaction_id[:8]}"
        elif transaction.status == TransactionStatus.FAILED:
            reason = transaction.failure_reason or "Declined by operator"
            msg = f"PayDay: Your {tx_type.lower()} of {amount_fmt} XAF via {tx_channel} was unsuccessful. Reason: {reason}"
        elif transaction.status == TransactionStatus.PROCESSING:
            msg = f"PayDay: Your {tx_type.lower()} of {amount_fmt} XAF via {tx_channel} is processing. Please confirm the prompt on your phone."
        else:
            msg = f"PayDay: Transaction status update for ref {transaction.transaction_id[:8]}: {transaction.status.value}."

        now = datetime.now(timezone.utc)

        # 1. SMS Notification
        sms = Notification(
            user_id=user.user_id,
            transaction_id=transaction.transaction_id,
            channel=NotificationChannel.SMS,
            recipient=user.phone_number,
            message=msg,
            status=NotificationStatus.SENT,
            sent_at=now,
        )
        db.add(sms)
        notifications.append(sms)

        # 2. Push Notification
        push = Notification(
            user_id=user.user_id,
            transaction_id=transaction.transaction_id,
            channel=NotificationChannel.PUSH,
            recipient=f"device-token-{user.user_id[:8]}",
            message=msg,
            status=NotificationStatus.SENT,
            sent_at=now,
        )
        db.add(push)
        notifications.append(push)

        await db.flush()
        logger.info(f"[NOTIFICATIONS] Dispatched SMS & Push for transaction {transaction.transaction_id} to {user.phone_number}")
        return notifications

    @staticmethod
    async def dispatch_kyc_alert(
        db: AsyncSession,
        user: User,
        status: str,
        reason: Optional[str] = None,
    ) -> List[Notification]:
        """Dispatches SMS and Push alerts when an admin reviews KYC submissions."""
        if status.upper() == "VERIFIED":
            msg = "PayDay: Congratulations! Your identity documents have been verified. Transaction limits unlocked."
        else:
            reason_txt = f" Reason: {reason}" if reason else ""
            msg = f"PayDay: Your KYC document submission was not approved.{reason_txt} Please re-submit your ID via the app."

        now = datetime.now(timezone.utc)
        sms = Notification(
            user_id=user.user_id,
            channel=NotificationChannel.SMS,
            recipient=user.phone_number,
            message=msg,
            status=NotificationStatus.SENT,
            sent_at=now,
        )
        db.add(sms)

        push = Notification(
            user_id=user.user_id,
            channel=NotificationChannel.PUSH,
            recipient=f"device-token-{user.user_id[:8]}",
            message=msg,
            status=NotificationStatus.SENT,
            sent_at=now,
        )
        db.add(push)

        await db.flush()
        return [sms, push]

    @staticmethod
    async def get_user_notifications(
        db: AsyncSession,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        channel: Optional[NotificationChannel] = None,
    ) -> Tuple[List[Notification], int]:
        """Queries notifications for the given user with pagination."""
        query = select(Notification).where(Notification.user_id == user_id)
        if channel:
            query = query.where(Notification.channel == channel)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar_one()

        query = query.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        items = (await db.execute(query)).scalars().all()

        return list(items), total


notification_service = NotificationService()
