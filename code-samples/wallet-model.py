"""
VaultPay Wallet — SQLAlchemy Model
====================================
Financial data modeling with SQLAlchemy 2.0 (async).

Key design decisions:

1. DECIMAL not FLOAT for money
   - `Numeric(precision=12, scale=2)` — 12 digits total, 2 decimal places
   - Floating-point is never acceptable for currency (binary rounding errors)

2. Human-readable wallet ID
   - `wallet_id` follows "VPY-XXXXXX" format (generated in service layer)
   - Separate from `id` (UUID primary key) — ID is internal, wallet_id is external

3. Status state machine
   - ACTIVE → FROZEN (PIN lockout, admin action)
   - ACTIVE → CLOSED (admin close)
   - FROZEN → ACTIVE (admin unfreeze, auto-unfreeze via cron)
   - CLOSED is terminal — no transitions out

4. Decimal fields as Python Decimal
   - SQLAlchemy maps Numeric → Python Decimal (exact arithmetic)
   - Never divide or multiply Decimal with float

5. user_id is intentionally NOT a ForeignKey
   - VaultPay and AuthShield have separate databases
   - user_id references AuthShield's users table which is unreachable
   - A DB-level FK would fail — use application-level integrity only

6. Timestamps
   - server_default=func.now() sets value in DB engine (timezone aware)
   - onupdate=func.now() auto-updates updated_at on every row change
"""

import uuid
from decimal import Decimal
from enum import Enum

from sqlalchemy import Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class WalletStatus(str, Enum):
    """
    Wallet state machine.
    Stored as a string enum in the DB for readability.
    """
    ACTIVE = "active"
    FROZEN = "frozen"   # Temporary — can be unfrozen
    CLOSED = "closed"   # Terminal — cannot be reopened


class Wallet(Base):
    """
    Wallet model — one per user, holds the user's balance.

    Relationships:
        Wallet.user_id → AuthShield.users.id (cross-service, no FK)
        Wallet → Transaction (one-to-many via wallet_id)
        Wallet → KYCSubmission (one-to-one via wallet_id)
    """
    __tablename__ = "wallets"

    # Primary key — UUID, internal use only
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Internal UUID primary key.",
    )

    # Human-readable wallet ID — "VPY-XXXXXX"
    # Generated in service layer, stored as unique identifier for P2P transfers
    wallet_id: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
        index=True,
        doc="Human-readable wallet ID (e.g. VPY-A1B2C3). Used for P2P transfers.",
    )

    # AuthShield user ID — cross-service reference, no DB FK
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,  # One wallet per user
        index=True,
        doc="AuthShield user UUID. No FK constraint (cross-DB).",
    )

    # Balance — DECIMAL for exact arithmetic
    # Constraints enforced in service layer (non-negative)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        default=Decimal("0.00"),
        doc="Current wallet balance (12 digits, 2 decimal places). Never a float.",
    )

    # Currency code — single currency per wallet
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="INR",
        doc="ISO 4217 currency code (e.g. INR, USD). Set at wallet creation, immutable.",
    )

    # Wallet state machine
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=WalletStatus.ACTIVE,
        doc="Wallet status: active | frozen | closed. See WalletStatus enum.",
    )

    # Transaction PIN — bcrypt hash, not the raw PIN
    # None = PIN not set yet (user must set before transacting)
    pin_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        doc="bcrypt-hashed transaction PIN. NULL = not set yet.",
    )

    # PIN attempt tracking for lockout logic
    pin_attempts: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        doc="Consecutive wrong PIN attempt count. Frozen at 3.",
    )

    # Audit fields
    created_at: Mapped[str] = mapped_column(
        nullable=False,
        server_default=func.now(),
        doc="Creation timestamp (UTC, set by DB server).",
    )
    updated_at: Mapped[str] = mapped_column(
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        doc="Last modification timestamp (UTC, auto-updates).",
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_wallets_user_id"),
        UniqueConstraint("wallet_id", name="uq_wallets_wallet_id"),
    )

    def __repr__(self) -> str:
        return f"<Wallet {self.wallet_id} user={self.user_id} balance={self.balance} {self.currency}>"
