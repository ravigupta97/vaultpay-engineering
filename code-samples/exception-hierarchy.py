"""
VaultPay Custom Exceptions
==========================
Centralized exception definitions for consistent error responses.

All exceptions:
  - Extend VaultPayException (the base)
  - Carry a message, error_code, and optional details dict
  - Are caught by exception handlers registered in main.py
  - Convert to standardized JSON responses

All error_codes follow the VP_ prefix convention.
HTTP status mapping lives in main.py's exception handlers.
"""

from typing import Any


class VaultPayException(Exception):
    """Base exception for all VaultPay errors."""

    def __init__(
        self,
        message: str,
        error_code: str,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.error_code = error_code
        self.details = details
        super().__init__(message)


# ── Authentication Errors (401) ──────────────────────────────────

class TokenMissingError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Authentication required. Please provide a Bearer token.",
            error_code="VP_TOKEN_MISSING",
        )


class TokenExpiredError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Token has expired. Please refresh your access token.",
            error_code="VP_TOKEN_EXPIRED",
        )


class TokenInvalidError(VaultPayException):
    def __init__(self, reason: str = "Token is invalid or malformed."):
        super().__init__(
            message=reason,
            error_code="VP_TOKEN_INVALID",
        )


# ── Authorization Errors (403) ──────────────────────────────────

class InsufficientPermissionsError(VaultPayException):
    def __init__(self, required_roles: list[str], user_roles: list[str]):
        super().__init__(
            message="You don't have permission to access this resource.",
            error_code="VP_INSUFFICIENT_PERMISSIONS",
            details={
                "required_roles": required_roles,
                "your_roles": user_roles,
            },
        )


class AccountDisabledError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Your account has been deactivated by AuthShield.",
            error_code="VP_ACCOUNT_DISABLED",
        )


class TokenRevokedError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Token has been revoked. Please log in again.",
            error_code="VP_TOKEN_REVOKED",
        )


# ── Service Errors (503) ────────────────────────────────────────

class AuthShieldUnavailableError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Authentication service is temporarily unavailable. Please try again later.",
            error_code="VP_AUTH_SERVICE_UNAVAILABLE",
        )


# ── Wallet Errors (400/404/409) ─────────────────────────────────

class WalletNotFoundError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Wallet not found. Please create a wallet first.",
            error_code="VP_WALLET_NOT_FOUND",
        )


class WalletAlreadyExistsError(VaultPayException):
    def __init__(self, wallet_id: str):
        super().__init__(
            message="You already have a wallet.",
            error_code="VP_WALLET_EXISTS",
            details={"wallet_id": wallet_id},
        )


class WalletFrozenError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Your wallet is frozen. Contact support or wait for auto-unfreeze.",
            error_code="VP_WALLET_FROZEN",
        )


class WalletClosedError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Your wallet has been closed and cannot perform operations.",
            error_code="VP_WALLET_CLOSED",
        )


# ── PIN Errors (400/403/429) ────────────────────────────────────

class PinNotSetError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Transaction PIN has not been set. Please set a PIN first.",
            error_code="VP_PIN_NOT_SET",
        )


class PinAlreadySetError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Transaction PIN is already set. Use change PIN to update.",
            error_code="VP_PIN_ALREADY_SET",
        )


class PinInvalidError(VaultPayException):
    def __init__(self, attempts_remaining: int | None = None):
        details = {"attempts_remaining": attempts_remaining} if attempts_remaining is not None else None
        super().__init__(
            message="Incorrect transaction PIN.",
            error_code="VP_PIN_INVALID",
            details=details,
        )


class PinMaxAttemptsError(VaultPayException):
    def __init__(self, freeze_hours: int = 24):
        super().__init__(
            message=f"Too many wrong PIN attempts. Wallet frozen for {freeze_hours} hours.",
            error_code="VP_PIN_MAX_ATTEMPTS",
            details={"freeze_hours": freeze_hours},
        )


# ── Transaction Errors (400) ────────────────────────────────────

class InsufficientBalanceError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="Insufficient balance for this transaction.",
            error_code="VP_INSUFFICIENT_BALANCE",
        )


class TransactionLimitExceededError(VaultPayException):
    def __init__(self, limit_type: str, limit_amount: float):
        super().__init__(
            message=f"{limit_type} limit exceeded.",
            error_code="VP_LIMIT_EXCEEDED",
            details={"limit_type": limit_type, "limit": limit_amount},
        )


class DuplicateTransactionError(VaultPayException):
    def __init__(self, transaction_ref: str):
        super().__init__(
            message="Duplicate transaction detected. This idempotency key has already been used.",
            error_code="VP_DUPLICATE_TRANSACTION",
            details={"existing_transaction_ref": transaction_ref},
        )


class UntrustedIPError(VaultPayException):
    def __init__(self, block_minutes: int):
        super().__init__(
            message=f"Transaction blocked from new IP. Check your email to confirm this IP (valid for {block_minutes} minutes).",
            error_code="VP_UNTRUSTED_IP",
            details={"block_minutes": block_minutes},
        )


class TransactionNotFoundError(VaultPayException):
    def __init__(self, ref: str):
        super().__init__(
            message="Transaction not found.",
            error_code="VP_TRANSACTION_NOT_FOUND",
            details={"transaction_ref": ref},
        )


# ── KYC Errors (400/409) ────────────────────────────────────────

class KYCAlreadySubmittedError(VaultPayException):
    def __init__(self, status: str):
        super().__init__(
            message=f"KYC already submitted (current status: {status}).",
            error_code="VP_KYC_ALREADY_SUBMITTED",
            details={"current_status": status},
        )


class KYCDuplicateIDError(VaultPayException):
    def __init__(self):
        super().__init__(
            message="This ID number is already registered with another account.",
            error_code="VP_KYC_DUPLICATE_ID",
        )


# ── Rate Limiting (429) ─────────────────────────────────────────

class RateLimitExceededError(VaultPayException):
    def __init__(self, retry_after: int = 60):
        super().__init__(
            message="Too many requests. Please slow down.",
            error_code="VP_RATE_LIMIT_EXCEEDED",
            details={"retry_after_seconds": retry_after},
        )
