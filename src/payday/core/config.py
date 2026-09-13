import os
from typing import List, Union
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "PayDay e-Wallet"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./payday.db"

    # Security & Tokens
    SECRET_KEY: str = "payday-super-secret-development-key-change-in-production-min32chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # AES-256 Field Encryption Key (Must be 32 bytes or string hashed to 32 bytes)
    ENCRYPTION_KEY: str = "payday_aes256_secret_key_32bytes!"

    # CORS
    # Explicit allowlist. Do NOT add "*" here: combined with
    # allow_credentials=True, Starlette reflects the caller's Origin back,
    # which lets any site issue credentialed cross-origin requests.
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://localhost:8080",
    ]

    # Pattern for ephemeral preview/sandbox hosts, which have no fixed origin.
    # Anchored so that e.g. "https://e2b.app.evil.com" does not match.
    BACKEND_CORS_ORIGIN_REGEX: str = r"^https://[a-z0-9-]+\.e2b\.app$"

    # Currency & Limits (XAF)
    DEFAULT_CURRENCY: str = "XAF"
    DEFAULT_DAILY_LIMIT: float = 500000.00
    DEFAULT_MONTHLY_LIMIT: float = 5000000.00
    MIN_TRANSACTION_AMOUNT: float = 100.00
    MAX_TRANSACTION_AMOUNT: float = 500000.00

    # Redis / shared counter store (WS-0)
    # The counter store backs login throttling and the PIN failure counter so
    # that multiple API replicas enforce ONE combined budget. "memory" is a
    # process-local store for dev/test only — it is never a fallback for redis.
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_REQUIRED: bool = False
    COUNTER_BACKEND: str = "memory"  # "memory" (dev/test) | "redis" (shared)
    # Peers allowed to supply X-Forwarded-For for rate-limit IP keys. Empty
    # means the header is never trusted and the socket peer is used.
    TRUSTED_PROXY_IPS: List[str] = []

    # Authentication rate limiting (WS-3 / LB-6)
    # Per-account limit counts attempts (login attempts, both failures and
    # successes); a successful login resets the per-account counter.
    LOGIN_RATE_LIMIT_PHONE: int = 5
    LOGIN_RATE_LIMIT_PHONE_WINDOW: int = 900     # 15 minutes
    LOGIN_RATE_LIMIT_IP: int = 20
    LOGIN_RATE_LIMIT_IP_WINDOW: int = 900        # 15 minutes
    REGISTER_RATE_LIMIT_IP: int = 10
    REGISTER_RATE_LIMIT_IP_WINDOW: int = 3600    # 1 hour
    REFRESH_RATE_LIMIT_IP: int = 10
    REFRESH_RATE_LIMIT_IP_WINDOW: int = 900      # 15 minutes

    # PIN brute-force lockout (WS-5 / LB-4)
    PIN_FAILURE_LIMIT: int = 5
    # The counter previously never expired (process-local dict). With Redis it
    # gains a TTL — a 24h window so old failures are forgiven (D-extra in the
    # launch-blocker roadmap).
    PIN_FAILURE_TTL_SECONDS: int = 86400

    # Fee Configuration
    DEFAULT_DEPOSIT_FEE_PERCENTAGE: float = 0.005  # 0.5%
    DEFAULT_WITHDRAW_FEE_PERCENTAGE: float = 0.01   # 1.0%
    MIN_FEE_AMOUNT: float = 25.00                  # 25 XAF minimum

    # XAF rounding (M1 / LB-9).
    # XAF has no minor unit, so a computed fee such as 61.72 must become a whole
    # number of francs. Which way it rounds is a commercial decision (D26); the
    # mode is configuration so the policy is explicit and reversible rather than
    # implied by a hard-coded quantize().
    XAF_ROUNDING_MODE: str = "HALF_UP"  # HALF_UP | HALF_EVEN | DOWN | UP

    # ------------------------------------------------------------------ #
    # Telco / mobile-money channels (M1 / LB-8)
    # ------------------------------------------------------------------ #
    # Without these settings the adapters were permanently in mock mode and the
    # live code paths were unreachable in production (both module singletons
    # were constructed with use_mock=True).
    #
    # Modes:
    #   mock    — deterministic in-process simulator (dev, tests, CI)
    #   sandbox — real operator sandbox APIs (self-provisioned MTN credentials)
    #   live    — real operator production APIs, moving real money
    TELCO_MODE: str = "mock"
    TELCO_HTTP_TIMEOUT_SECONDS: float = 15.0
    # Refresh an operator token this many seconds before it expires (they are
    # typically valid for one hour; the previous cache never expired at all).
    TELCO_TOKEN_REFRESH_SKEW_SECONDS: int = 300
    # Absolute, publicly reachable base URL of this API. Required in live mode:
    # operator callbacks cannot reach a relative path.
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    # Reject a number whose prefix belongs to a different operator. Off by
    # default because Cameroon has number portability and published prefix
    # tables disagree — see core/msisdn.py.
    TELCO_STRICT_OPERATOR_PREFIX: bool = False

    # --- MTN MoMo ---------------------------------------------------------- #
    # Collections and disbursements use SEPARATE API users, keys and
    # subscription keys. In production all of them are issued by MTN after
    # KYC/contract; sandbox credentials are self-provisioned.
    MTN_BASE_URL: str = ""            # defaults per mode; required in live
    MTN_TARGET_ENVIRONMENT: str = ""  # "sandbox" or the country env, e.g. mtncameroon
    MTN_COLLECTION_SUBSCRIPTION_KEY: str = ""
    MTN_COLLECTION_API_USER: str = ""
    MTN_COLLECTION_API_KEY: str = ""
    MTN_DISBURSEMENT_SUBSCRIPTION_KEY: str = ""
    MTN_DISBURSEMENT_API_USER: str = ""
    MTN_DISBURSEMENT_API_KEY: str = ""

    # --- Orange Money ------------------------------------------------------ #
    ORANGE_TOKEN_URL: str = "https://api.orange.com/oauth/v3/token"
    ORANGE_BASE_URL: str = ""  # defaults per mode; required in live
    # Orange may issue a ready-made Basic value (preferred when present) or a
    # client_id/client_secret pair to encode. The previous code sent
    # "Basic {id}:{secret}" unencoded, which is not a valid Authorization header.
    ORANGE_AUTH_HEADER: str = ""
    ORANGE_CLIENT_ID: str = ""
    ORANGE_CLIENT_SECRET: str = ""
    ORANGE_MERCHANT_KEY: str = ""
    # Status- and payout-path suffixes differ across Orange API generations and
    # the API is mid-migration; kept configurable rather than hard-coded so the
    # sandbox run (task A5) can correct them without a code change.
    ORANGE_STATUS_PATH: str = "transactionstatus"
    ORANGE_PAYOUT_PATH: str = "payout"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow"
    )

    # ------------------------------------------------------------------ #
    # Derived telco values
    # ------------------------------------------------------------------ #
    @property
    def mtn_base_url(self) -> str:
        """MTN base URL for the configured mode.

        Sandbox has a fixed public host. Production hosts are issued by MTN
        (`api.mtn.com`, or the regional proxy) and therefore must be configured
        explicitly — guessing one would be a silent misroute of real money.
        """
        if self.MTN_BASE_URL:
            return self.MTN_BASE_URL.rstrip("/")
        if self.TELCO_MODE == "sandbox":
            return "https://sandbox.momodeveloper.mtn.com"
        return ""

    @property
    def mtn_target_environment(self) -> str:
        """`X-Target-Environment`: `sandbox`, or the country environment.

        Production for Cameroon is `mtncameroon`; it is required in live mode
        because omitting the header makes the gateway return 500s.
        """
        if self.MTN_TARGET_ENVIRONMENT:
            return self.MTN_TARGET_ENVIRONMENT
        return "sandbox" if self.TELCO_MODE == "sandbox" else ""

    @property
    def orange_base_url(self) -> str:
        """Orange Web Payment base URL for the configured mode.

        The dev and production paths differ only in the version segment.
        """
        if self.ORANGE_BASE_URL:
            return self.ORANGE_BASE_URL.rstrip("/")
        if self.TELCO_MODE == "sandbox":
            return "https://api.orange.com/orange-money-webpay/dev/v1"
        return "https://api.orange.com/orange-money-webpay/cm/v1"

    @property
    def orange_uses_preissued_auth_header(self) -> bool:
        """True when Orange supplied a ready-to-send Authorization value."""
        return bool(self.ORANGE_AUTH_HEADER)

    @property
    def public_base_url(self) -> str:
        return self.PUBLIC_BASE_URL.rstrip("/")

    def mtn_callback_url(self) -> str:
        return f"{self.public_base_url}{self.API_V1_STR}/webhooks/mtn"

    def orange_notification_url(self) -> str:
        return f"{self.public_base_url}{self.API_V1_STR}/webhooks/orange"


class TelcoConfigurationError(RuntimeError):
    """Raised when the telco configuration would move money unsafely."""


def _is_public_https(url: str) -> bool:
    return url.startswith("https://") and "localhost" not in url and "127.0.0.1" not in url


def validate_telco_configuration(settings_: "Settings | None" = None) -> list[str]:
    """Validate the telco configuration; raise when it is unsafe to start.

    Follows the WS-0 fail-closed precedent (`core/counters.py`): misconfiguration
    that would silently fake money movement refuses startup rather than logging a
    warning nobody reads. The rules:

    1. `ENVIRONMENT=production` with `TELCO_MODE != live` is fatal. A production
       deployment running the mock adapter accepts deposits that never happen and
       never reconciles — the worst possible failure for a wallet.
    2. `TELCO_MODE=live` with missing credentials is fatal, per channel, with the
       missing setting names listed.
    3. `TELCO_MODE=live` with a non-public `PUBLIC_BASE_URL` is fatal: operator
       callbacks could never arrive, so transactions would hang in PROCESSING.
    4. Anything else (mock, sandbox) is allowed and returns warnings, so local
       development and CI need no credentials.

    Returns the list of warnings for logging by the caller.
    """
    s = settings_ or settings
    mode = s.TELCO_MODE
    if mode not in {"mock", "sandbox", "live"}:
        raise TelcoConfigurationError(
            f"TELCO_MODE={mode!r} is invalid; expected one of mock, sandbox, live."
        )

    if s.ENVIRONMENT == "production" and mode != "live":
        raise TelcoConfigurationError(
            f"ENVIRONMENT=production with TELCO_MODE={mode!r} — refusing to start. "
            "A production wallet must not run against mock payment channels: "
            "deposits would be accepted and never settled. Set TELCO_MODE=live "
            "and provide operator credentials."
        )

    warnings: list[str] = []

    if mode == "live":
        missing: list[str] = []

        for label, keys in (
            (
                "MTN",
                [
                    "MTN_BASE_URL",
                    "MTN_TARGET_ENVIRONMENT",
                    "MTN_COLLECTION_SUBSCRIPTION_KEY",
                    "MTN_COLLECTION_API_USER",
                    "MTN_COLLECTION_API_KEY",
                    "MTN_DISBURSEMENT_SUBSCRIPTION_KEY",
                    "MTN_DISBURSEMENT_API_USER",
                    "MTN_DISBURSEMENT_API_KEY",
                ],
            ),
            (
                "Orange",
                ["ORANGE_MERCHANT_KEY"],
            ),
        ):
            for key in keys:
                if not getattr(s, key):
                    missing.append(f"{label}: {key}")

        if not s.orange_uses_preissued_auth_header and not (
            s.ORANGE_CLIENT_ID and s.ORANGE_CLIENT_SECRET
        ):
            missing.append(
                "Orange: ORANGE_AUTH_HEADER (or ORANGE_CLIENT_ID + ORANGE_CLIENT_SECRET)"
            )

        if missing:
            raise TelcoConfigurationError(
                "TELCO_MODE=live is missing required operator configuration:\n  - "
                + "\n  - ".join(missing)
            )

        if not _is_public_https(s.public_base_url):
            raise TelcoConfigurationError(
                f"TELCO_MODE=live requires a public HTTPS PUBLIC_BASE_URL (got "
                f"{s.public_base_url!r}). Operator callbacks cannot reach a local "
                "or non-TLS address, so transactions would never leave PROCESSING."
            )

        # Interlock (M1 task A8). MTN does not sign its callbacks and Orange
        # verifies by echoing a per-order notif_token; neither control is
        # implemented yet, so both adapters reject live callbacks rather than
        # trusting them. Refusing to start makes it impossible to take real money
        # with an unverified callback path — the failure mode this project keeps
        # discovering. Remove this gate when A8 lands.
        raise TelcoConfigurationError(
            "TELCO_MODE=live is not yet available: operator callback verification "
            "(M1 task A8) is unimplemented, so inbound callbacks are rejected and "
            "transactions could never settle. Run the operator sandbox "
            "(TELCO_MODE=sandbox) until A8 lands — see docs/plans/M1_LIVE_MONEY_PATH_PLAN.md."
        )
    else:
        if mode == "sandbox" and not s.MTN_COLLECTION_API_USER:
            warnings.append(
                "TELCO_MODE=sandbox but MTN sandbox credentials are absent; MTN calls "
                "will fail until they are set (sandbox credentials are self-provisioned)."
            )
        if s.ENVIRONMENT == "production":
            warnings.append("ENVIRONMENT=production is not using live telco mode.")

    return warnings


settings = Settings()
