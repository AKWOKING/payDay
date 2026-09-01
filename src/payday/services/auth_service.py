from datetime import timedelta
from decimal import Decimal
from typing import Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from payday.core.config import settings
from payday.core.security import (
    get_password_hash,
    verify_password,
    get_pin_hash,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from payday.core.encryption import encryption_service
from payday.core.exceptions import (
    UserAlreadyExistsError,
    AuthenticationError,
    UserNotFoundError,
    InvalidPinError,
    PermissionDeniedError,
)
from payday.models.user import User, KycStatus, UserRole, UserStatus
from payday.models.wallet import Wallet, WalletStatus
from payday.schemas.auth import RegisterRequest, LoginRequest, SetPinRequest, TokenResponse
from payday.services.audit_service import audit_service


class AuthService:
    @staticmethod
    async def register_user(db: AsyncSession, req: RegisterRequest) -> Tuple[User, Wallet]:
        # Check if phone number already exists
        result = await db.execute(select(User).where(User.phone_number == req.phone_number))
        if result.scalars().first():
            raise UserAlreadyExistsError(f"A user with phone number '{req.phone_number}' is already registered.")

        # Check email if provided
        if req.email:
            result = await db.execute(select(User).where(User.email == req.email))
            if result.scalars().first():
                raise UserAlreadyExistsError(f"A user with email '{req.email}' is already registered.")

        # Encrypt the ID document number using AES-256-GCM
        encrypted_id = encryption_service.encrypt(req.id_document_no)
        password_hash = get_password_hash(req.password)

        # Create user
        new_user = User(
            full_name=req.full_name,
            phone_number=req.phone_number,
            email=req.email,
            password_hash=password_hash,
            id_document_no_encrypted=encrypted_id,
            id_document_type=req.id_document_type,
            kyc_status=KycStatus.PENDING,
            role=UserRole.CUSTOMER,
            status=UserStatus.ACTIVE,
        )
        db.add(new_user)
        await db.flush()  # Flush to generate user_id

        # Automatically create Central XAF Wallet
        new_wallet = Wallet(
            user_id=new_user.user_id,
            balance=Decimal("0.00"),
            locked_balance=Decimal("0.00"),
            currency=settings.DEFAULT_CURRENCY,
            status=WalletStatus.ACTIVE,
            daily_limit=Decimal(str(settings.DEFAULT_DAILY_LIMIT)),
            monthly_limit=Decimal(str(settings.DEFAULT_MONTHLY_LIMIT)),
        )
        db.add(new_wallet)
        await db.flush()

        await audit_service.log_action(
            db=db,
            action="USER_REGISTERED",
            entity_name="User",
            entity_id=new_user.user_id,
            actor_id=new_user.user_id,
            new_state={
                "phone_number": new_user.phone_number,
                "wallet_id": new_wallet.wallet_id,
            },
        )
        await db.commit()
        await db.refresh(new_user)
        await db.refresh(new_wallet)
        return new_user, new_wallet

    @staticmethod
    async def login_user(db: AsyncSession, req: LoginRequest) -> TokenResponse:
        result = await db.execute(select(User).where(User.phone_number == req.phone_number))
        user = result.scalars().first()
        if not user or not verify_password(req.password, user.password_hash):
            raise AuthenticationError("Invalid phone number or password")

        if user.status != UserStatus.ACTIVE:
            raise PermissionDeniedError(f"Account is {user.status.value.lower()}. Please contact support.")

        access_token = create_access_token(subject=user.user_id, role=user.role.value)
        refresh_token = create_refresh_token(subject=user.user_id, role=user.role.value)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user_id=user.user_id,
            role=user.role.value,
            has_pin=bool(user.pin_hash),
            kyc_status=user.kyc_status.value,
        )

    @staticmethod
    async def refresh_tokens(db: AsyncSession, refresh_token: str) -> TokenResponse:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise AuthenticationError("Invalid token type. Expected refresh token.")

        user_id = payload.get("sub")
        result = await db.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()
        if not user or user.status != UserStatus.ACTIVE:
            raise AuthenticationError("User not found or inactive")

        access_token = create_access_token(subject=user.user_id, role=user.role.value)
        new_refresh_token = create_refresh_token(subject=user.user_id, role=user.role.value)

        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user_id=user.user_id,
            role=user.role.value,
            has_pin=bool(user.pin_hash),
            kyc_status=user.kyc_status.value,
        )

    @staticmethod
    async def set_transaction_pin(db: AsyncSession, user_id: str, req: SetPinRequest) -> User:
        result = await db.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()
        if not user:
            raise UserNotFoundError()

        if not verify_password(req.password, user.password_hash):
            raise AuthenticationError("Invalid account password for PIN confirmation")

        user.pin_hash = get_pin_hash(req.pin)
        await audit_service.log_action(
            db=db,
            action="PIN_CONFIGURED",
            entity_name="User",
            entity_id=user.user_id,
            actor_id=user.user_id,
        )
        await db.commit()
        await db.refresh(user)
        return user


auth_service = AuthService()
