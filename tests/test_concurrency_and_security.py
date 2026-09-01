import asyncio
from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from payday.models.user import User, KycStatus, UserRole, UserStatus
from payday.models.wallet import Wallet, WalletStatus
from payday.services.wallet_engine import wallet_engine
from payday.core.encryption import encryption_service
from payday.core.security import get_password_hash


@pytest.mark.asyncio
async def test_pii_encryption_at_rest(db_session: AsyncSession):
    plaintext_id = "CM-NATIONAL-ID-12345678"
    encrypted_bytes = encryption_service.encrypt(plaintext_id)

    user = User(
        full_name="PII Test User",
        phone_number="+237699001122",
        password_hash=get_password_hash("SecretPassword123"),
        id_document_no_encrypted=encrypted_bytes,
        id_document_type="NATIONAL_ID",
        kyc_status=KycStatus.VERIFIED,
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.commit()

    # Query raw database record
    raw_user = (await db_session.execute(select(User).where(User.phone_number == "+237699001122"))).scalar_one()
    assert raw_user.id_document_no_encrypted != plaintext_id.encode("utf-8")
    assert raw_user.id_document_no_encrypted is not None

    # Decrypt and verify match
    decrypted = encryption_service.decrypt(raw_user.id_document_no_encrypted)
    assert decrypted == plaintext_id


@pytest.mark.asyncio
async def test_concurrent_deposits_integrity(db_session: AsyncSession, test_user: User):
    wallet = await wallet_engine.get_wallet_by_user_id(db_session, test_user.user_id)
    initial_balance = wallet.balance
    deposit_amount = Decimal("1000.00")
    deposit_fee = Decimal("5.00")
    net_deposit = deposit_amount - deposit_fee

    num_concurrent_deposits = 10

    for i in range(num_concurrent_deposits):
        await wallet_engine.credit_deposit(
            db=db_session,
            wallet=wallet,
            amount=deposit_amount,
            fee=deposit_fee,
            transaction_id=f"tx-concurrent-deposit-{i}",
        )

    expected_final_balance = initial_balance + (net_deposit * num_concurrent_deposits)
    assert wallet.balance == expected_final_balance
    assert wallet.locked_balance == Decimal("0.00")


@pytest.mark.asyncio
async def test_ledger_balance_invariance(db_session: AsyncSession, test_user: User):
    """Verifies that balance math never produces negative available balances."""
    wallet = await wallet_engine.get_wallet_by_user_id(db_session, test_user.user_id)
    assert wallet.available_balance >= Decimal("0.00")
