import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Union
import bcrypt
from jose import jwt, JWTError
from payday.core.config import settings
from payday.core.exceptions import AuthenticationError


def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8")[:72],
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def get_pin_hash(pin: str) -> str:
    return get_password_hash(pin)


def verify_pin(plain_pin: str, hashed_pin: str) -> bool:
    if not hashed_pin:
        return False
    return verify_password(plain_pin, hashed_pin)


def create_access_token(
    subject: Union[str, Any],
    role: str,
    token_version: int,
    expires_delta: Optional[timedelta] = None,
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "sub": str(subject),
        "role": role,
        "tv": int(token_version),
        "type": "access",
        "exp": expire,
        "iat": datetime.now(timezone.utc)
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_refresh_token(
    subject: Union[str, Any],
    role: str,
    token_version: int,
    expires_delta: Optional[timedelta] = None,
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    to_encode = {
        "sub": str(subject),
        "role": role,
        "tv": int(token_version),
        "type": "refresh",
        "exp": expire,
        "iat": datetime.now(timezone.utc)
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def token_version_matches(payload: Dict[str, Any], current_token_version: int) -> bool:
    """True when a decoded token was issued at the user's current token version.

    `token_version` is the counter incremented by `AuthService.revoke_all_sessions`.
    A token minted before a revocation carries a lower `tv` claim and is refused
    — which is what makes logout, admin suspension and password reset evict
    sessions instead of merely asking clients to forget them.

    Tokens minted before WS-2 shipped carry no `tv` claim at all; they are read
    as version 0 rather than rejected, so deploying this change does not log
    every user out. That is not a weaker rule: any revocation moves the account
    above 0, and those tokens are then refused along with everything else. A
    non-numeric `tv` is a mismatch (fail closed) rather than an error.
    """
    try:
        return int(payload.get("tv", 0)) == int(current_token_version)
    except (TypeError, ValueError):
        return False


def decode_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        raise AuthenticationError("Could not validate credentials or token expired")


def normalize_cameroon_phone(phone: str) -> str:
    """
    Standardizes Cameroon MSISDNs.
    Valid formats: 6XXXXXXXX, 2376XXXXXXXX, +2376XXXXXXXX.
    Output: +2376XXXXXXXX
    """
    cleaned = re.sub(r"[\s\-\(\)]", "", phone)
    if cleaned.startswith("+237"):
        digits = cleaned[4:]
    elif cleaned.startswith("237"):
        digits = cleaned[3:]
    else:
        digits = cleaned

    if not re.match(r"^6[5-9]\d{7}$|^62\d{7}$|^2[234]\d{7}$", digits):
        # We allow standard 9-digit Cameroon numbers starting with 6 or 2
        if len(digits) != 9 or not digits.isdigit():
            raise ValueError(f"Invalid Cameroon phone number format: '{phone}'. Expected 9 digits (e.g. +2376XXXXXXXX).")
    
    return f"+237{digits}"
