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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow"
    )


settings = Settings()
