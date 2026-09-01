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
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://localhost:8080",
        "https://*.e2b.app",
        "*"
    ]

    # Currency & Limits (XAF)
    DEFAULT_CURRENCY: str = "XAF"
    DEFAULT_DAILY_LIMIT: float = 500000.00
    DEFAULT_MONTHLY_LIMIT: float = 5000000.00
    MIN_TRANSACTION_AMOUNT: float = 100.00
    MAX_TRANSACTION_AMOUNT: float = 500000.00

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
