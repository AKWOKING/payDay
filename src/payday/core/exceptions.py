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
            status.HTTP_422_UNPROCESSABLE_ENTITY: "Unprocessable Entity",
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


class DuplicateTransactionError(PayDayException):
    def __init__(self, idempotency_key: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A transaction with idempotency key '{idempotency_key}' is already being processed or completed.",
            code="DUPLICATE_TRANSACTION",
            title="Duplicate Request",
        )
