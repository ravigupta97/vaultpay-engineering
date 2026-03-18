"""
VaultPay Standardized API Response Schemas
==========================================
All VaultPay endpoints return responses wrapped in a consistent envelope.

SUCCESS Response:
    {
        "status": "success",
        "message": "Wallet created successfully",
        "data": { ... }
    }

ERROR Response:
    {
        "status": "error",
        "message": "Insufficient balance.",
        "error_code": "VP_INSUFFICIENT_BALANCE",
        "details": { "key": "value" }
    }

WHY STANDARDIZE?
  - Frontend knows exactly what to expect from every endpoint
  - Machine-parseable error codes enable programmatic handling
  - Matches AuthShield's response format for consistency across services
  - Generic[T] gives full Pydantic validation of the `data` field

Usage in endpoints:
    @router.post("/wallet", response_model=StandardResponse[WalletOut])
    async def create_wallet(...):
        wallet = await create_wallet_service(...)
        return StandardResponse(message="Wallet created successfully", data=wallet)

Usage with pagination:
    @router.get("/transactions", response_model=PaginatedResponse[TransactionOut])
    async def list_transactions(params: PaginationParams = Depends()):
        txns, total = await fetch_transactions(offset=params.offset, limit=params.limit)
        return PaginatedResponse(
            message="Transaction history",
            data=txns,
            total=total,
            page=params.page,
            per_page=params.per_page,
        )
"""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


# ── Success Responses ─────────────────────────────────────────────

class StandardResponse(BaseModel, Generic[T]):
    """Standard single-object success response wrapper."""
    status: str = "success"
    message: str
    data: T | None = None

    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Paginated list response.
    `total_pages` is auto-calculated in model_post_init.
    """
    status: str = "success"
    message: str
    data: list[T]
    total: int
    page: int
    per_page: int
    total_pages: int = 0

    def model_post_init(self, __context: Any) -> None:
        """Calculate total_pages after initialization."""
        if self.per_page > 0:
            self.total_pages = (self.total + self.per_page - 1) // self.per_page


class ErrorResponse(BaseModel):
    """
    Error response schema — for OpenAPI documentation only.
    Exception handlers return JSONResponse directly.
    """
    status: str = "error"
    message: str
    error_code: str
    details: dict[str, Any] | None = None


# ── Common Query Parameters ──────────────────────────────────────

class PaginationParams(BaseModel):
    """
    Reusable pagination parameters with computed offset/limit.

    Usage:
        @router.get("/transactions")
        async def list_txns(params: PaginationParams = Depends()):
            items, total = await fetch(
                offset=params.offset,
                limit=params.limit,
            )
    """
    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    per_page: int = Field(default=20, ge=1, le=100, description="Items per page (max 100)")

    @property
    def offset(self) -> int:
        """SQL OFFSET value."""
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        """SQL LIMIT value."""
        return self.per_page


# ── Timestamp Schema Mixin ───────────────────────────────────────

class TimestampSchema(BaseModel):
    """Mixin for schemas that include created_at/updated_at."""
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
