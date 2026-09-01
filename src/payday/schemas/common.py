from typing import Generic, TypeVar, Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field

T = TypeVar("T")


class ProblemDetail(BaseModel):
    """RFC 7807 Error Response structure."""
    type: str = Field(default="https://payday.cm/errors/general", description="URI reference identifying the problem type")
    title: str = Field(..., description="Short, human-readable summary of the problem")
    status: int = Field(..., description="HTTP status code")
    detail: str = Field(..., description="Human-readable explanation specific to this occurrence")
    instance: Optional[str] = Field(None, description="URI reference identifying the specific occurrence")
    code: str = Field(default="PAYDAY_ERROR", description="Domain-specific error code")
    extra: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional debugging or context parameters")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class APIResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str = "Operation completed successfully"
    data: Optional[T] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int
