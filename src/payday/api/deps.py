from typing import List
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from payday.core.database import get_db
from payday.core.security import decode_token, token_version_matches
from payday.core.exceptions import (
    AuthenticationError,
    PermissionDeniedError,
    SessionRevokedError,
    UserNotFoundError,
    KycRequiredError,
)
from payday.models.user import User, UserRole, UserStatus, KycStatus

security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    token_auth: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not token_auth:
        raise AuthenticationError("Authorization header missing or invalid")

    payload = decode_token(token_auth.credentials)
    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type. Expected access token.")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Token payload missing subject identifier")

    result = await db.execute(select(User).where(User.user_id == user_id))
    user = result.scalars().first()
    if not user:
        raise UserNotFoundError("User associated with this token no longer exists")

    if user.status != UserStatus.ACTIVE:
        raise PermissionDeniedError(f"User account is {user.status.value.lower()}")

    # WS-2 / LB-7: the token must have been minted at the account's current
    # token version. A logout, admin suspension or password reset increments
    # it, so every token issued before that moment stops working here — the
    # access token included, not just the refresh token. Checked after the
    # status check so a suspended account still reports suspension rather than
    # a generic revocation.
    if not token_version_matches(payload, user.token_version):
        raise SessionRevokedError()

    return user


async def get_current_verified_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.kyc_status != KycStatus.VERIFIED:
        raise KycRequiredError("KYC verification is required to perform this action")
    return current_user


def require_roles(*required_roles: UserRole):
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in required_roles:
            raise PermissionDeniedError(
                f"Requires one of roles: {[r.value for r in required_roles]}. Current: {current_user.role.value}"
            )
        return current_user
    return role_checker
