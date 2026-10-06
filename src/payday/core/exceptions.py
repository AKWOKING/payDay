from typing import Optional, Dict, Any
from fastapi import HTTPException, status


class PayDayException(HTTPException):
    """Base domain exception following RFC 7807 standard."""
    def __init__(
        self,
        status_code: int,
        detail: str,
        code: str = "PAYDAY_ERROR",
        title: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.title = title or self._default_title(status_code)
        self.extra = extra or {}

    @staticmethod
    def _default_title(status_code: int) -> str:
        titles = {
            status.HTTP_400_BAD_REQUEST: "Bad Request",
            status.HTTP_401_UNAUTHORIZED: "Unauthorized",
            status.HTTP_403_FORBIDDEN: "Forbidden",
            status.HTTP_404_NOT_FOUND: "Not Found",
            status.HTTP_409_CONFLICT: "Conflict",
            422: "Unprocessable Content",
            status.HTTP_500_INTERNAL_SERVER_ERROR: "Internal Server Error",
        }
        return titles.get(status_code, "Error")


class AuthenticationError(PayDayException):
    def __init__(self, detail: str = "Invalid credentials or expired token"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            code="AUTHENTICATION_FAILED",
            title="Authentication Error",
            headers={"WWW-Authenticate": "Bearer"},
        )


class SessionRevokedError(PayDayException):
    """401 — the token was minted before the account's last session revocation.

    Deliberately distinct from `AuthenticationError`: clients can then tell
    "your session was ended deliberately, sign in again" (logout elsewhere,
    admin suspension, password reset) from "this token expired", and support
    can see which of the two actually happened.
    """

    def __init__(self, detail: str = "Session has been revoked. Please sign in again."):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            code="SESSION_REVOKED",
            title="Session Revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )


class PermissionDeniedError(PayDayException):
    def __init__(self, detail: str = "You do not have permission to access this resource"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            code="PERMISSION_DENIED",
            title="Access Denied",
        )


class UserNotFoundError(PayDayException):
    def __init__(self, detail: str = "User account not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
            code="USER_NOT_FOUND",
            title="User Not Found",
        )


class UserAlreadyExistsError(PayDayException):
    def __init__(self, detail: str = "A user with this phone number or email already exists"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            code="USER_ALREADY_EXISTS",
            title="User Already Exists",
        )


class WalletNotFoundError(PayDayException):
    def __init__(self, detail: str = "Wallet account not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
            code="WALLET_NOT_FOUND",
            title="Wallet Not Found",
        )


class WalletFrozenError(PayDayException):
    def __init__(self, detail: str = "This wallet is currently frozen or closed"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            code="WALLET_FROZEN",
            title="Wallet Suspended",
        )


class InsufficientFundsError(PayDayException):
    def __init__(self, available: float, required: float):
        detail = f"Insufficient wallet balance. Available: {available:.2f} XAF, Required (with fees): {required:.2f} XAF."
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            code="INSUFFICIENT_FUNDS",
            title="Insufficient Funds",
            extra={"available_balance": available, "required_amount": required},
        )


class DailyLimitExceededError(PayDayException):
    def __init__(self, limit: float, current_total: float, requested: float):
        detail = f"Daily transaction limit ({limit:.2f} XAF) exceeded. Today's volume: {current_total:.2f} XAF, Requested: {requested:.2f} XAF."
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            code="DAILY_LIMIT_EXCEEDED",
            title="Limit Exceeded",
            extra={"daily_limit": limit, "current_total": current_total, "requested": requested},
        )


class MonthlyLimitExceededError(PayDayException):
    def __init__(self, limit: float, current_total: float, requested: float):
        detail = f"Monthly transaction limit ({limit:.2f} XAF) exceeded."
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            code="MONTHLY_LIMIT_EXCEEDED",
            title="Limit Exceeded",
            extra={"monthly_limit": limit, "current_total": current_total, "requested": requested},
        )


class InvalidPinError(PayDayException):
    def __init__(self, detail: str = "Invalid transaction PIN"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            code="INVALID_PIN",
            title="Invalid PIN",
        )


class PinNotSetError(PayDayException):
    def __init__(self, detail: str = "Transaction PIN has not been configured for this account"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            code="PIN_NOT_SET",
            title="PIN Required",
        )


class KycRequiredError(PayDayException):
    def __init__(self, detail: str = "Verified KYC status is required to perform this transaction"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            code="KYC_REQUIRED",
            title="KYC Verification Required",
        )


class BalanceCeilingExceededError(PayDayException):
    """A credit would push a wallet past its maximum stored value.

    E-money wallets have a ceiling. The published figure and the enforced one
    must be the same number, so the error names the ceiling rather than saying
    "limit exceeded" and leaving support to guess which limit.
    """

    def __init__(self, ceiling: float, current_balance: float, attempted_credit: float):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"This wallet cannot hold more than {ceiling:,.0f} XAF. "
                f"Balance {current_balance:,.0f} XAF + {attempted_credit:,.0f} XAF "
                f"would exceed it."
            ),
            code="BALANCE_CEILING_EXCEEDED",
            title="Wallet Balance Ceiling Reached",
            extra={
                "ceiling": ceiling,
                "current_balance": current_balance,
                "attempted_credit": attempted_credit,
            },
        )


class RecipientNotFoundError(PayDayException):
    """The transfer recipient is not a PayDay user (and no channel was given)."""

    def __init__(self, recipient: str, suggested_channel: Optional[str] = None):
        hint = (
            f" Supply channel={suggested_channel} to send to their mobile money "
            f"account instead."
            if suggested_channel
            else " Supply a channel to send to their mobile money account instead."
        )
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{recipient} does not have a PayDay wallet.{hint} The channel is a "
                "hint: numbers can be ported between operators, so the sender must "
                "choose it."
            ),
            code="RECIPIENT_NOT_ON_PAYDAY",
            title="Recipient Not Found",
            extra={"suggested_channel": suggested_channel},
        )


class SelfTransferError(PayDayException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot send money to your own wallet.",
            code="SELF_TRANSFER",
            title="Invalid Recipient",
        )


class DuplicateTransactionError(PayDayException):
    def __init__(self, idempotency_key: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A transaction with idempotency key '{idempotency_key}' is already being processed or completed.",
            code="DUPLICATE_TRANSACTION",
            title="Duplicate Request",
        )


class InvalidStateTransitionError(PayDayException):
    def __init__(self, current_status: str, target_status: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Illegal transaction state transition from '{current_status}' to '{target_status}'.",
            code="INVALID_STATE_TRANSITION",
            title="Invalid State Transition",
            extra={"current_status": current_status, "target_status": target_status},
        )


class RateLimitError(PayDayException):
    """429 — WS-3 / LB-6: an authentication endpoint was hit too often."""

    def __init__(self, limit: int, retry_after: int):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many requests. Try again in {max(1, retry_after)} seconds.",
            code="RATE_LIMITED",
            title="Rate Limit Exceeded",
            headers={"Retry-After": str(max(1, retry_after))},
            extra={"limit": limit, "retry_after_seconds": max(1, retry_after)},
        )


class RedisUnavailableError(PayDayException):
    """503 — the shared counter store is down; operations fail closed."""

    def __init__(self, detail: str = "Shared state store is unavailable. Please retry later."):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
            code="REDIS_UNAVAILABLE",
            title="Service Unavailable",
            headers={"Retry-After": "5"},
        )
