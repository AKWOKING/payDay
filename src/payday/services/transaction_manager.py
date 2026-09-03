import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Set, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from payday.models.user import User, KycStatus, UserStatus
from payday.models.wallet import Wallet, WalletStatus
from payday.models.linked_account import LinkedExternalAccount, ChannelProvider
from payday.models.transaction import (
    Transaction,
    TransactionType,
    TransactionChannel,
    TransactionStatus,
)
from payday.models.notification import Notification, NotificationChannel, NotificationStatus
from payday.schemas.transaction import (
    DepositInitiateRequest,
    WithdrawInitiateRequest,
    TransactionReceiptResponse,
    WebhookCallbackPayload,
)
from payday.services.wallet_engine import wallet_engine
from payday.services.audit_service import audit_service
from payday.adapters.factory import adapter_factory
from payday.adapters.base import ChannelDepositRequest, ChannelWithdrawalRequest
from payday.core.security import verify_pin
from payday.core.exceptions import (
    PayDayException,
    InvalidPinError,
    PinNotSetError,
    WalletFrozenError,
    UserNotFoundError,
    DuplicateTransactionError,
    InvalidStateTransitionError,
)
from payday.core.logging import logger


class TransactionManager:
    """
    Orchestrates money-movement operations, external channel adapter requests,
    webhook callback processing, and the transaction state machine.
    """

    # In-memory tracker for consecutive failed PIN attempts (backed by Redis in distributed clusters)
    _failed_pin_attempts: Dict[str, int] = {}

    # Allowed valid transitions in the transaction lifecycle
    ALLOWED_TRANSITIONS = {
        TransactionStatus.PENDING: {TransactionStatus.PROCESSING, TransactionStatus.FAILED},
        TransactionStatus.PROCESSING: {TransactionStatus.SUCCESS, TransactionStatus.FAILED},
        TransactionStatus.SUCCESS: {TransactionStatus.REVERSED},
        TransactionStatus.FAILED: set(),    # Terminal state
        TransactionStatus.REVERSED: set(),  # Terminal state
    }

    @classmethod
    def validate_state_transition(cls, current: TransactionStatus, target: TransactionStatus) -> None:
        """Validates that state transition obeys the strict state machine rules."""
        if current == target:
            return  # Idempotent replay

        allowed = cls.ALLOWED_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidStateTransitionError(current_status=current.value, target_status=target.value)

    @staticmethod
    async def _get_or_create_linked_account(
        db: AsyncSession,
        user_id: str,
        provider: ChannelProvider,
        identifier: str,
    ) -> LinkedExternalAccount:
        result = await db.execute(
            select(LinkedExternalAccount).where(
                LinkedExternalAccount.user_id == user_id,
                LinkedExternalAccount.provider == provider,
                LinkedExternalAccount.account_identifier == identifier,
            )
        )
        linked = result.scalars().first()
        if not linked:
            try:
                async with db.begin_nested():
                    linked = LinkedExternalAccount(
                        user_id=user_id,
                        provider=provider,
                        account_identifier=identifier,
                        is_verified=True,
                        is_default=False,
                    )
                    db.add(linked)
                    await db.flush()
            except Exception:
                result = await db.execute(
                    select(LinkedExternalAccount).where(
                        LinkedExternalAccount.user_id == user_id,
                        LinkedExternalAccount.provider == provider,
                        LinkedExternalAccount.account_identifier == identifier,
                    )
                )
                linked = result.scalars().first()
        return linked

    @staticmethod
    async def initiate_deposit(
        db: AsyncSession,
        user: User,
        req: DepositInitiateRequest,
    ) -> Transaction:
        # Check user status
        if user.status != UserStatus.ACTIVE:
            raise PayDayException(status_code=403, detail="User account is not active", code="ACCOUNT_INACTIVE")

        # Check wallet
        user_wallet = await wallet_engine.get_wallet_by_user_id(db, user.user_id)
        wallet = await wallet_engine.get_wallet_with_lock(db, user_wallet.wallet_id)
        wallet_id = wallet.wallet_id

        # Idempotency check
        idempotency_key = req.idempotency_key or str(uuid.uuid4())
        existing_tx = (await db.execute(select(Transaction).where(Transaction.idempotency_key == idempotency_key))).scalars().first()
        if existing_tx:
            return existing_tx

        # Phone number resolution
        payer_phone = req.phone_number or user.phone_number
        channel_provider = ChannelProvider[req.channel.value]
        linked_account = await TransactionManager._get_or_create_linked_account(
            db=db,
            user_id=user.user_id,
            provider=channel_provider,
            identifier=payer_phone,
        )
        linked_account_id = linked_account.linked_account_id if linked_account else None

        # Calculate fees
        fee = wallet_engine.calculate_fee(TransactionType.DEPOSIT, req.amount)
        net_amount = (req.amount - fee).quantize(Decimal("0.01"))

        # Generate transaction ID up front
        tx_id = str(uuid.uuid4())

        # Call Channel Adapter (MTN / Orange)
        adapter = adapter_factory.get_adapter(req.channel.value)
        deposit_payload = ChannelDepositRequest(
            transaction_id=tx_id,
            phone_number=payer_phone,
            amount=req.amount,
            description=f"PayDay deposit of {req.amount} XAF",
        )

        channel_res = await adapter.initiate_deposit(deposit_payload)
        external_ref = channel_res.channel_ref
        completed_at = None
        failure_reason = None

        if channel_res.success:
            status = TransactionStatus.PROCESSING
            if channel_res.status == "SUCCESS" or (channel_res.raw_response.get("auto_finalize")):
                await wallet_engine.credit_deposit(
                    db=db,
                    wallet=wallet,
                    amount=req.amount,
                    fee=fee,
                    transaction_id=tx_id,
                )
                status = TransactionStatus.SUCCESS
                completed_at = datetime.now(timezone.utc)
        else:
            status = TransactionStatus.FAILED
            failure_reason = channel_res.message
            completed_at = datetime.now(timezone.utc)

        # Create Transaction atomically
        transaction = Transaction(
            transaction_id=tx_id,
            idempotency_key=idempotency_key,
            wallet_id=wallet_id,
            linked_account_id=linked_account_id,
            type=TransactionType.DEPOSIT,
            channel=req.channel,
            amount=req.amount,
            fee=fee,
            net_amount=net_amount,
            status=status,
            external_ref=external_ref,
            failure_reason=failure_reason,
            completed_at=completed_at,
        )

        try:
            async with db.begin_nested():
                db.add(transaction)
                await db.flush()
        except Exception:
            existing_tx = (await db.execute(select(Transaction).where(Transaction.idempotency_key == idempotency_key))).scalars().first()
            if existing_tx:
                return existing_tx
            raise

        await audit_service.log_action(
            db=db,
            action=f"DEPOSIT_{transaction.status.value}",
            entity_name="Transaction",
            entity_id=transaction.transaction_id,
            actor_id=user.user_id,
            new_state={
                "amount": str(req.amount),
                "fee": str(fee),
                "status": transaction.status.value,
                "external_ref": transaction.external_ref,
            },
        )

        # Dispatch Transaction Notification
        from payday.services.notification_service import notification_service
        await notification_service.dispatch_transaction_alert(
            db=db,
            user=user,
            transaction=transaction,
            current_balance=float(wallet.balance),
        )

        await db.commit()
        await db.refresh(transaction)
        return transaction

    @staticmethod
    async def initiate_withdrawal(
        db: AsyncSession,
        user: User,
        req: WithdrawInitiateRequest,
    ) -> Transaction:
        # Check user status & PIN
        if user.status != UserStatus.ACTIVE:
            raise PayDayException(status_code=403, detail="User account is not active", code="ACCOUNT_INACTIVE")

        if not user.pin_hash:
            raise PinNotSetError()

        if not verify_pin(req.pin, user.pin_hash):
            current_fails = TransactionManager._failed_pin_attempts.get(user.user_id, 0) + 1
            TransactionManager._failed_pin_attempts[user.user_id] = current_fails
            logger.warning(f"[SECURITY] Failed PIN attempt {current_fails}/5 for user {user.user_id}")

            if current_fails >= 5:
                # 5th failed attempt -> Auto-freeze wallet
                user_wallet = await wallet_engine.get_wallet_by_user_id(db, user.user_id)
                wallet = await wallet_engine.get_wallet_with_lock(db, user_wallet.wallet_id, require_active=False)
                wallet.status = WalletStatus.FROZEN
                
                # Log audit trail
                await audit_service.log_action(
                    db=db,
                    action="WALLET_AUTO_FROZEN_PIN_BRUTE_FORCE",
                    entity_name="Wallet",
                    entity_id=wallet.wallet_id,
                    actor_id=user.user_id,
                    new_state={"status": WalletStatus.FROZEN.value, "failed_attempts": current_fails},
                )
                
                # Dispatch Security Notification
                from payday.services.notification_service import notification_service
                await notification_service.dispatch_security_alert(
                    db=db,
                    user=user,
                    message="PayDay Security Alert: Your wallet has been suspended due to 5 consecutive failed PIN attempts. Please contact support to verify identity.",
                )
                await db.commit()
                raise WalletFrozenError("Your wallet has been suspended due to 5 consecutive failed PIN attempts.")

            raise InvalidPinError(f"Invalid transaction PIN. Attempt {current_fails} of 5.")

        # On valid PIN, clear failed attempts
        TransactionManager._failed_pin_attempts.pop(user.user_id, None)

        # Idempotency check
        idempotency_key = req.idempotency_key or str(uuid.uuid4())
        existing_tx = (await db.execute(select(Transaction).where(Transaction.idempotency_key == idempotency_key))).scalars().first()
        if existing_tx:
            return existing_tx

        # Acquire lock on wallet
        user_wallet = await wallet_engine.get_wallet_by_user_id(db, user.user_id)
        wallet = await wallet_engine.get_wallet_with_lock(db, user_wallet.wallet_id)
        wallet_id = wallet.wallet_id

        # Calculate fees
        fee = wallet_engine.calculate_fee(TransactionType.WITHDRAW, req.amount)
        net_amount = req.amount

        # Resolve destination linked account
        channel_provider = ChannelProvider[req.channel.value]
        linked_account = await TransactionManager._get_or_create_linked_account(
            db=db,
            user_id=user.user_id,
            provider=channel_provider,
            identifier=req.destination_phone,
        )
        linked_account_id = linked_account.linked_account_id if linked_account else None

        # Generate tx_id
        tx_id = str(uuid.uuid4())

        # Hold funds on wallet ledger (Increments locked_balance)
        await wallet_engine.hold_funds(
            db=db,
            wallet=wallet,
            amount=req.amount,
            fee=fee,
            transaction_id=tx_id,
        )

        # Call Channel Adapter for Disbursement
        adapter = adapter_factory.get_adapter(req.channel.value)
        withdrawal_payload = ChannelWithdrawalRequest(
            transaction_id=tx_id,
            destination_phone=req.destination_phone,
            amount=req.amount,
            description=f"PayDay withdrawal of {req.amount} XAF",
        )

        channel_res = await adapter.initiate_withdrawal(withdrawal_payload)
        external_ref = channel_res.channel_ref
        completed_at = None
        failure_reason = None

        if channel_res.success:
            status = TransactionStatus.PROCESSING
            # In mock or immediate mode, finalize withdrawal
            if channel_res.status == "SUCCESS" or (channel_res.raw_response.get("auto_finalize")):
                await wallet_engine.finalize_withdrawal(
                    db=db,
                    wallet=wallet,
                    amount=req.amount,
                    fee=fee,
                    transaction_id=tx_id,
                )
                status = TransactionStatus.SUCCESS
                completed_at = datetime.now(timezone.utc)
        else:
            # External call failed -> Release hold automatically
            await wallet_engine.release_hold(
                db=db,
                wallet=wallet,
                amount=req.amount,
                fee=fee,
                transaction_id=tx_id,
                reason=channel_res.message,
            )
            status = TransactionStatus.FAILED
            failure_reason = channel_res.message
            completed_at = datetime.now(timezone.utc)

        # Create Transaction atomically
        transaction = Transaction(
            transaction_id=tx_id,
            idempotency_key=idempotency_key,
            wallet_id=wallet_id,
            linked_account_id=linked_account_id,
            type=TransactionType.WITHDRAW,
            channel=req.channel,
            amount=req.amount,
            fee=fee,
            net_amount=net_amount,
            status=status,
            external_ref=external_ref,
            failure_reason=failure_reason,
            completed_at=completed_at,
        )

        try:
            async with db.begin_nested():
                db.add(transaction)
                await db.flush()
        except Exception:
            existing_tx = (await db.execute(select(Transaction).where(Transaction.idempotency_key == idempotency_key))).scalars().first()
            if existing_tx:
                return existing_tx
            raise

        await audit_service.log_action(
            db=db,
            action=f"WITHDRAWAL_{transaction.status.value}",
            entity_name="Transaction",
            entity_id=transaction.transaction_id,
            actor_id=user.user_id,
            new_state={
                "amount": str(req.amount),
                "fee": str(fee),
                "status": transaction.status.value,
                "external_ref": transaction.external_ref,
            },
        )

        from payday.services.notification_service import notification_service
        await notification_service.dispatch_transaction_alert(
            db=db,
            user=user,
            transaction=transaction,
            current_balance=float(wallet.balance),
        )

        await db.commit()
        await db.refresh(transaction)
        return transaction

    @staticmethod
    async def process_webhook(
        db: AsyncSession,
        channel: TransactionChannel,
        payload: WebhookCallbackPayload,
    ) -> Transaction:
        """
        Processes an asynchronous status callback from MTN or Orange Money.
        Applies state machine transitions: PROCESSING -> SUCCESS / FAILED.
        """
        query = select(Transaction)
        if payload.transaction_id:
            query = query.where(Transaction.transaction_id == payload.transaction_id)
        else:
            query = query.where(Transaction.external_ref == payload.external_ref)

        result = await db.execute(query)
        transaction = result.scalars().first()

        if not transaction:
            raise PayDayException(
                status_code=404,
                detail=f"Transaction with reference '{payload.external_ref or payload.transaction_id}' not found.",
                code="TRANSACTION_NOT_FOUND",
            )

        # Idempotency check: if transaction is already in final state, return as-is
        if transaction.status in (TransactionStatus.SUCCESS, TransactionStatus.FAILED, TransactionStatus.REVERSED):
            logger.info(f"[WEBHOOK] Transaction {transaction.transaction_id} already finalized ({transaction.status.value}). Acknowledging duplicate.")
            return transaction

        # Determine target state
        target_status = TransactionStatus.SUCCESS if payload.status.upper() in ("SUCCESSFUL", "SUCCESS") else TransactionStatus.FAILED
        
        # Enforce State Machine Validation
        TransactionManager.validate_state_transition(transaction.status, target_status)

        # Lock wallet
        wallet = await wallet_engine.get_wallet_with_lock(db, transaction.wallet_id)

        if target_status == TransactionStatus.SUCCESS:
            if transaction.type == TransactionType.DEPOSIT:
                await wallet_engine.credit_deposit(
                    db=db,
                    wallet=wallet,
                    amount=transaction.amount,
                    fee=transaction.fee,
                    transaction_id=transaction.transaction_id,
                )
            elif transaction.type == TransactionType.WITHDRAW:
                await wallet_engine.finalize_withdrawal(
                    db=db,
                    wallet=wallet,
                    amount=transaction.amount,
                    fee=transaction.fee,
                    transaction_id=transaction.transaction_id,
                )
            transaction.status = TransactionStatus.SUCCESS
            transaction.completed_at = datetime.now(timezone.utc)
        else:
            # FAILED or REJECTED
            if transaction.type == TransactionType.WITHDRAW:
                # Release held funds
                await wallet_engine.release_hold(
                    db=db,
                    wallet=wallet,
                    amount=transaction.amount,
                    fee=transaction.fee,
                    transaction_id=transaction.transaction_id,
                    reason=payload.reason or "Partner rejected or timed out",
                )
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = payload.reason or "Partner transaction failure"
            transaction.completed_at = datetime.now(timezone.utc)

        await audit_service.log_action(
            db=db,
            action=f"WEBHOOK_{transaction.status.value}",
            entity_name="Transaction",
            entity_id=transaction.transaction_id,
            new_state={
                "status": transaction.status.value,
                "partner_status": payload.status,
                "external_ref": payload.external_ref,
            },
        )

        # Dispatch Notification on Webhook Finalization
        user_res = await db.execute(select(User).where(User.user_id == wallet.user_id))
        target_user = user_res.scalars().first()
        if target_user:
            from payday.services.notification_service import notification_service
            await notification_service.dispatch_transaction_alert(
                db=db,
                user=target_user,
                transaction=transaction,
                current_balance=float(wallet.balance),
            )

        await db.commit()
        await db.refresh(transaction)
        return transaction

    @staticmethod
    async def get_receipt(db: AsyncSession, user: User, transaction_id: str) -> TransactionReceiptResponse:
        user_wallet = await wallet_engine.get_wallet_by_user_id(db, user.user_id)
        result = await db.execute(
            select(Transaction).where(
                Transaction.transaction_id == transaction_id,
                Transaction.wallet_id == user_wallet.wallet_id,
            )
        )
        tx = result.scalars().first()
        if not tx:
            raise PayDayException(status_code=404, detail="Transaction not found", code="TRANSACTION_NOT_FOUND")

        total_charged = tx.amount + (tx.fee if tx.type == TransactionType.WITHDRAW else Decimal("0.00"))
        net_credited = (tx.amount - tx.fee) if tx.type == TransactionType.DEPOSIT else tx.amount

        message = "Transaction completed successfully." if tx.status == TransactionStatus.SUCCESS else (
            "Transaction is processing." if tx.status == TransactionStatus.PROCESSING else f"Transaction failed: {tx.failure_reason or 'Declined'}"
        )

        return TransactionReceiptResponse(
            transaction_id=tx.transaction_id,
            idempotency_key=tx.idempotency_key,
            user_name=user.full_name,
            user_phone=user.phone_number,
            type=tx.type,
            channel=tx.channel,
            currency="XAF",
            amount=tx.amount,
            fee=tx.fee,
            total_charged=total_charged,
            net_credited=net_credited,
            status=tx.status,
            external_ref=tx.external_ref,
            created_at=tx.created_at,
            completed_at=tx.completed_at,
            message=message,
        )


transaction_manager = TransactionManager()
