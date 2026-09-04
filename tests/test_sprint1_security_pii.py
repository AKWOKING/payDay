import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from payday.models.user import User, KycStatus, UserRole, UserStatus
from payday.core.encryption import encryption_service
from payday.core.security import get_password_hash, verify_password, get_pin_hash, verify_pin


@pytest.mark.asyncio
async def test_raw_database_pii_encryption(db_session: AsyncSession):
    """
    Inspects the raw database record to ensure sensitive customer KYC details
    (National ID / Passport) are stored as AES-256 binary cipher text,
    preventing plain-text leakage during database backups, logs, or exports.
    """
    raw_id_document = "CAMEROON-ID-2026-9988776655"
    encrypted_bytes = encryption_service.encrypt(raw_id_document)

    user = User(
        full_name="Security Audit Customer",
        phone_number="+237699778899",
        password_hash=get_password_hash("StrongSecret@2026"),
        pin_hash=get_pin_hash("9876"),
        id_document_no_encrypted=encrypted_bytes,
        id_document_type="NATIONAL_ID",
        kyc_status=KycStatus.VERIFIED,
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.commit()

    # Query directly from DB
    result = await db_session.execute(select(User).where(User.phone_number == "+237699778899"))
    db_user = result.scalar_one()

    # 1. Assert raw bytes are not plaintext
    assert db_user.id_document_no_encrypted is not None
    assert isinstance(db_user.id_document_no_encrypted, bytes)
    assert raw_id_document.encode("utf-8") not in db_user.id_document_no_encrypted

    # 2. Assert decryption recovers the original exact ID
    decrypted = encryption_service.decrypt(db_user.id_document_no_encrypted)
    assert decrypted == raw_id_document


def test_password_and_pin_salting_and_hashing():
    """
    Verifies that Bcrypt salting & hashing:
    - Generates standard Bcrypt hash format ($2b$...)
    - Generates unique salts (different hashes for identical inputs)
    - Verifies valid credentials and rejects incorrect ones
    """
    pin = "5432"
    hash1 = get_pin_hash(pin)
    hash2 = get_pin_hash(pin)

    # 1. Format check
    assert hash1.startswith("$2b$")
    assert hash2.startswith("$2b$")

    # 2. Unique Salt Check: two hashes of the same PIN must NOT be equal
    assert hash1 != hash2

    # 3. Verification checks
    assert verify_pin(pin, hash1) is True
    assert verify_pin(pin, hash2) is True
    assert verify_pin("0000", hash1) is False
    assert verify_pin("5431", hash1) is False

    # 4. Password check
    password = "MyComplexPassword#2026"
    pwd_hash1 = get_password_hash(password)
    pwd_hash2 = get_password_hash(password)
    assert pwd_hash1 != pwd_hash2
    assert verify_password(password, pwd_hash1) is True
    assert verify_password("WrongPassword", pwd_hash1) is False
