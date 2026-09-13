from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from payday.core.config import settings
from payday.core.database import get_db
from payday.core.ratelimit import (
    enforce_rate_limit,
    get_client_ip,
    login_ip_key,
    login_phone_key,
    refresh_ip_key,
    register_ip_key,
    reset_rate_limit,
)
from payday.schemas.common import APIResponse
from payday.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    SetPinRequest,
)
from payday.schemas.user import UserResponse
from payday.services.auth_service import auth_service
from payday.api.deps import get_current_user
from payday.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication & Profile"])


@router.post(
    "/register",
    response_model=APIResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="Register New User & Create Central Wallet",
    description="Registers a new customer, hashes credentials, encrypts KYC document number, and auto-generates their primary XAF wallet.",
)
async def register(req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # Throttle registration spam per source IP (WS-3). No per-phone bucket here:
    # duplicate registrations must keep returning 409, not 429.
    await enforce_rate_limit(
        register_ip_key(get_client_ip(request)),
        settings.REGISTER_RATE_LIMIT_IP,
        settings.REGISTER_RATE_LIMIT_IP_WINDOW,
    )
    user, wallet = await auth_service.register_user(db, req)
    return APIResponse(
        success=True,
        message="User registered successfully. Central XAF wallet generated.",
        data={
            "user_id": user.user_id,
            "full_name": user.full_name,
            "phone_number": user.phone_number,
            "wallet_id": wallet.wallet_id,
            "currency": wallet.currency,
            "balance": str(wallet.balance),
            "kyc_status": user.kyc_status.value,
        },
    )


@router.post(
    "/login",
    response_model=APIResponse[TokenResponse],
    summary="User Login",
    description="Authenticates user with phone number and password. Returns JWT access token (15 mins) and refresh token (7 days).",
)
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # LB-6: login was completely unthrottled. Two independent buckets:
    # per-phone-number (defeats one-account spraying from many IPs) and
    # per-source-IP (defeats one-host spraying of many accounts).
    await enforce_rate_limit(
        login_phone_key(req.phone_number),
        settings.LOGIN_RATE_LIMIT_PHONE,
        settings.LOGIN_RATE_LIMIT_PHONE_WINDOW,
    )
    await enforce_rate_limit(
        login_ip_key(get_client_ip(request)),
        settings.LOGIN_RATE_LIMIT_IP,
        settings.LOGIN_RATE_LIMIT_IP_WINDOW,
    )

    tokens = await auth_service.login_user(db, req)

    # A successful login resets the per-account failure counter (not the
    # per-IP bucket, which remains the windowed budget for the whole host).
    await reset_rate_limit(login_phone_key(req.phone_number))
    return APIResponse(
        success=True,
        message="Login successful",
        data=tokens,
    )


@router.post(
    "/refresh",
    response_model=APIResponse[TokenResponse],
    summary="Refresh Access Token",
    description="Issues a fresh access token using a valid refresh token.",
)
async def refresh_token(req: RefreshTokenRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # Throttle token refresh per source IP (WS-3).
    await enforce_rate_limit(
        refresh_ip_key(get_client_ip(request)),
        settings.REFRESH_RATE_LIMIT_IP,
        settings.REFRESH_RATE_LIMIT_IP_WINDOW,
    )
    tokens = await auth_service.refresh_tokens(db, req.refresh_token)
    return APIResponse(
        success=True,
        message="Token refreshed successfully",
        data=tokens,
    )


@router.post(
    "/logout",
    response_model=APIResponse[dict],
    summary="Logout (Revoke All Sessions)",
    description=(
        "Server-side logout. Increments the account's token version, which "
        "immediately invalidates every access and refresh token issued before "
        "this request — **including the one used to call it**. The client must "
        "discard its stored tokens; a later refresh returns 401 SESSION_REVOKED. "
        "Revocation is account-wide (coarse), so signing out on one device signs "
        "the account out on all of them."
    ),
)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    token_version = await auth_service.revoke_all_sessions(
        db,
        current_user.user_id,
        reason="USER_LOGOUT",
        actor_id=current_user.user_id,
    )
    return APIResponse(
        success=True,
        message="Signed out. All sessions for this account have been revoked.",
        data={
            "user_id": current_user.user_id,
            "sessions_revoked": True,
            "token_version": token_version,
        },
    )


@router.post(
    "/set-pin",
    response_model=APIResponse[dict],
    summary="Set or Update Transaction PIN",
    description="Configures a 4 to 6 digit numeric transaction PIN required for money movement operations.",
)
async def set_pin(
    req: SetPinRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await auth_service.set_transaction_pin(db, current_user.user_id, req)
    return APIResponse(
        success=True,
        message="Transaction PIN configured successfully",
        data={"user_id": current_user.user_id, "has_pin": True},
    )


@router.get(
    "/me",
    response_model=APIResponse[UserResponse],
    summary="Get Current User Profile",
    description="Fetches authenticated user profile, KYC status, and PIN configuration flag.",
)
async def get_me(current_user: User = Depends(get_current_user)):
    user_response = UserResponse(
        user_id=current_user.user_id,
        full_name=current_user.full_name,
        phone_number=current_user.phone_number,
        email=current_user.email,
        kyc_status=current_user.kyc_status,
        role=current_user.role,
        status=current_user.status,
        has_pin=bool(current_user.pin_hash),
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )
    return APIResponse(success=True, data=user_response)
