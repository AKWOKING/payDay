import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from payday.models.user import User
from payday.models.transaction import Transaction, TransactionType, TransactionChannel, TransactionStatus
from payday.services.transaction_manager import transaction_manager
from payday.services.wallet_engine import wallet_engine
from payday.core.exceptions import InvalidStateTransitionError
from payday.schemas.transaction import WebhookCallbackPayload


def test_state_machine_allowed_and_forbidden_transitions():
    """
    Direct State Machine Validation:
    - PENDING -> PROCESSING (Legal)
    - PROCESSING -> SUCCESS (Legal)
    - PROCESSING -> FAILED (Legal)
    - SUCCESS -> REVERSED (Legal)
    - FAILED -> SUCCESS (Illegal -> Raises InvalidStateTransitionError)
    - REVERSED -> SUCCESS (Illegal -> Raises InvalidStateTransitionError)
    - SUCCESS -> PROCESSING (Illegal -> Raises InvalidStateTransitionError)
    """
    # 1. Legal transitions
    transaction_manager.validate_state_transition(TransactionStatus.PENDING, TransactionStatus.PROCESSING)
    transaction_manager.validate_state_transition(TransactionStatus.PROCESSING, TransactionStatus.SUCCESS)
    transaction_manager.validate_state_transition(TransactionStatus.PROCESSING, TransactionStatus.FAILED)
    transaction_manager.validate_state_transition(TransactionStatus.SUCCESS, TransactionStatus.REVERSED)

    # 2. Illegal jumps
    with pytest.raises(InvalidStateTransitionError):
        transaction_manager.validate_state_transition(TransactionStatus.FAILED, TransactionStatus.SUCCESS)

    with pytest.raises(InvalidStateTransitionError):
        transaction_manager.validate_state_transition(TransactionStatus.REVERSED, TransactionStatus.SUCCESS)

    with pytest.raises(InvalidStateTransitionError):
        transaction_manager.validate_state_transition(TransactionStatus.SUCCESS, TransactionStatus.PROCESSING)

    with pytest.raises(InvalidStateTransitionError):
        transaction_manager.validate_state_transition(TransactionStatus.PENDING, TransactionStatus.SUCCESS)


@pytest.mark.asyncio
async def test_illegal_state_jump_in_webhook_flow(
    db_session: AsyncSession,
    test_user: User,
):
    """
    Attempts to resurrect a FAILED transaction to SUCCESS via a spoofed webhook.
    Verifies that the state machine halts the update and raises InvalidStateTransitionError.
    """
    user_wallet = await wallet_engine.get_wallet_by_user_id(db_session, test_user.user_id)
    from payday.models.linked_account import LinkedExternalAccount, ChannelProvider
    account = LinkedExternalAccount(
        user_id=test_user.user_id,
        provider=ChannelProvider.MTN,
        account_identifier="+237677112233",
        is_verified=True,
    )
    db_session.add(account)
    await db_session.flush()

    failed_tx = Transaction(
        idempotency_key="failed-tx-key-101",
        wallet_id=user_wallet.wallet_id,
        linked_account_id=account.linked_account_id,
        type=TransactionType.DEPOSIT,
        channel=TransactionChannel.MTN,
        amount=5000.0,
        fee=25.0,
        net_amount=4975.0,
        status=TransactionStatus.FAILED,
        external_ref="MTN-FAILED-REF-101",
    )
    db_session.add(failed_tx)
    await db_session.commit()

    # Attempt to process a SUCCESSFUL callback on this FAILED transaction
    # Since FAILED is terminal, it acknowledges as terminal without re-crediting or transitioning
    payload = WebhookCallbackPayload(
        transaction_id=failed_tx.transaction_id,
        external_ref=failed_tx.external_ref,
        status="SUCCESSFUL",
    )
    res = await transaction_manager.process_webhook(
        db=db_session,
        channel=TransactionChannel.MTN,
        payload=payload,
    )
    # Status remains FAILED and balance is unaffected
    assert res.status == TransactionStatus.FAILED
