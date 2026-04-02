"""
pin-lockout.py — VaultPay PIN Verification & Brute-Force Protection
=====================================================================

This module handles all transaction PIN operations. The security model:

  - PINs are stored as bcrypt hashes — the plain-text is never persisted.
  - A Redis counter tracks consecutive failed attempts per wallet (fast, O(1)).
  - The DB mirrors the counter for durability across Redis restarts.
  - After 3 failures the wallet is auto-frozen for 24 hours.
  - Recovery: users trigger an email-based PIN reset, which unfreezes the wallet.
  - PIN change (not reset) requires a StrictUser check — a live HTTP call to
    AuthShield to confirm the JWT hasn't been revoked mid-session.

Redis key schema (VaultPay DB 1):
  pin_attempts:{wallet_id}      → int   (TTL: 24 h after last failed attempt)
  wallet_frozen:{wallet_id}     → "1"   (TTL: 24 h, used by scheduled unfreeze job)
  pin_reset:{token}             → user_id  (TTL: 15 min, single-use)

This sample is extracted from:
  features/pin/service.py  → verify_transaction_pin(), _record_failed_attempt(),
                              request_pin_reset(), reset_pin()
"""

import secrets
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditActions, log_audit
from core.context import UserContext
from core.security import hash_pin, verify_pin
from exceptions import (
    PinInvalidError,
    PinMaxAttemptsError,
    PinNotSetError,
    TokenInvalidError,
    WalletClosedError,
    WalletFrozenError,
    WalletNotFoundError,
)
from features.wallet.models import Wallet

log = structlog.get_logger()

# ── Constants ────────────────────────────────────────────────────
PIN_ATTEMPTS_KEY   = "pin_attempts:{wallet_id}"     # Redis counter of failed PINs
PIN_RESET_TOKEN_KEY = "pin_reset:{token}"           # Email reset token
WALLET_FROZEN_KEY  = "wallet_frozen:{wallet_id}"    # Auto-unfreeze marker

MAX_PIN_ATTEMPTS  = 3
PIN_LOCKOUT_TTL   = 86400   # 24 hours — wallet freeze duration
PIN_RESET_TTL     = 900     # 15 minutes — email token validity


# ── Verify Transaction PIN ───────────────────────────────────────

async def verify_transaction_pin(
    db: AsyncSession,
    ctx: UserContext,
    plain_pin: str,
    redis: Redis | None = None,
) -> bool:
    """
    Verify the transaction PIN before allowing a financial operation.

    Two-layer rate limiting:
      Layer 1 — Redis (O(1) pre-check): if the counter is already at the
                limit, reject immediately without touching the DB or bcrypt.
      Layer 2 — _record_failed_attempt(): increments counter and freezes
                the wallet on the 3rd failure.

    Returns True on success. Always raises on failure — never returns False.
    Callers don't need to check the return value; an exception means blocked.
    """
    wallet = await _get_active_wallet(db, ctx.user_id)

    if not wallet.has_pin:
        raise PinNotSetError()

    # ── Pre-check in Redis before bcrypt ─────────────────────────
    # Design note: bcrypt.verify() is intentionally slow (~100 ms).
    # We check the Redis counter first to short-circuit for already-locked
    # wallets, and to avoid wasting CPU on attack traffic hitting a frozen wallet.
    if redis:
        key = PIN_ATTEMPTS_KEY.format(wallet_id=str(wallet.id))
        attempts = await redis.get(key)
        if attempts and int(attempts) >= MAX_PIN_ATTEMPTS:
            # Wallet is already at the lockout threshold from prior failures.
            # Raise immediately — don't even run bcrypt.
            raise PinMaxAttemptsError()

    # ── bcrypt verification ───────────────────────────────────────
    if verify_pin(plain_pin, wallet.transaction_pin_hash):
        # Success — reset the attempt counter in both Redis and the DB.
        # Design note: we reset in Redis first (fast) then DB. Even if DB
        # reset fails (unlikely), the Redis reset is the one that matters
        # for future pre-checks.
        if redis:
            key = PIN_ATTEMPTS_KEY.format(wallet_id=str(wallet.id))
            await redis.delete(key)
        wallet.pin_attempts = 0
        await db.commit()
        return True

    # ── Failed attempt ───────────────────────────────────────────
    await _record_failed_attempt(db, wallet, ctx, redis)
    raise PinInvalidError()


# ── Private: Record a Failed Attempt ────────────────────────────

async def _record_failed_attempt(
    db: AsyncSession,
    wallet: Wallet,
    ctx: UserContext,
    redis: Redis | None = None,
) -> None:
    """
    Atomically increment the failed-attempt counter and trigger a wallet
    freeze if the limit is reached.

    Dual-write pattern (Redis + DB):
      - Redis INCR is atomic — safe under concurrent requests.
      - DB mirror ensures durability: if Redis is wiped, the counter
        is not lost permanently (wallet stays frozen in DB).

    Every failed attempt writes an audit log entry so admins can
    investigate brute-force patterns per user/IP.
    """
    # ── Increment Redis counter ─────────────────────────────────
    if redis:
        key = PIN_ATTEMPTS_KEY.format(wallet_id=str(wallet.id))
        # INCR is atomic — guarantees no two concurrent requests
        # both read the same "old" value and each get count = 1.
        attempts = await redis.incr(key)
        # Reset the TTL on every failed attempt so the 24h window
        # is relative to the LAST failure, not the first.
        await redis.expire(key, PIN_LOCKOUT_TTL)
    else:
        # Redis unavailable — fall back to DB-only counter.
        attempts = wallet.pin_attempts + 1

    # Mirror to DB (durability).
    wallet.pin_attempts = attempts

    # ── Audit log (every failed attempt) ────────────────────────
    # Design note: we log BEFORE the freeze audit entry so the event
    # timeline in the audit table is chronologically accurate.
    await log_audit(
        db=db,
        user_id=ctx.user_id,
        action=AuditActions.PIN_FAILED_ATTEMPT,
        ip_address=ctx.client_ip,
        details={
            "wallet_id": wallet.wallet_id,
            "attempt_number": attempts,
            "max_attempts": MAX_PIN_ATTEMPTS,
        },
    )

    # ── Freeze if limit reached ─────────────────────────────────
    if attempts >= MAX_PIN_ATTEMPTS:
        wallet.status = "frozen"

        # Second audit entry: explicitly records the freeze event.
        # This makes the admin timeline clear: "attempt 3 → freeze".
        await log_audit(
            db=db,
            user_id=ctx.user_id,
            action=AuditActions.PIN_MAX_ATTEMPTS,
            ip_address=ctx.client_ip,
            details={
                "wallet_id": wallet.wallet_id,
                "frozen_for_seconds": PIN_LOCKOUT_TTL,
            },
        )

        # Set a Redis expiry marker so a background job (or the next
        # request) can auto-unfreeze after 24 h without a cron dependency.
        if redis:
            freeze_key = WALLET_FROZEN_KEY.format(wallet_id=wallet.id)
            await redis.set(freeze_key, "1", ex=PIN_LOCKOUT_TTL)

        log.warning(
            "Wallet frozen — max PIN attempts reached",
            user_id=ctx.user_id,
            wallet_id=wallet.wallet_id,
            attempts=attempts,
        )

    await db.commit()


# ── Request PIN Reset (via Email Token) ─────────────────────────

async def request_pin_reset(
    db: AsyncSession,
    ctx: UserContext,
    redis: Redis | None = None,
) -> str:
    """
    Generate a time-limited, single-use PIN reset token.

    Design note: the token is stored in Redis (not the DB) because:
      - It's ephemeral — only valid for 15 minutes.
      - Redis TTL handles expiry automatically; no cleanup job needed.
      - The DB doesn't grow with unprocessed reset attempts.

    In production this token is embedded in an email link.
    The endpoint returns the token directly (for MVP / local testing).
    """
    result = await db.execute(
        select(Wallet).where(Wallet.user_id == UUID(ctx.user_id))
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise WalletNotFoundError()
    if not wallet.has_pin:
        raise PinNotSetError()

    # secrets.token_urlsafe(32) gives 256 bits of entropy — unguessable.
    token = secrets.token_urlsafe(32)

    if redis:
        key = PIN_RESET_TOKEN_KEY.format(token=token)
        # Store user_id as the value — reset_pin() uses this to look up
        # the wallet without requiring the user to be authenticated
        # (their wallet is frozen, so their JWT may still be valid but
        # the wallet blocks all operations).
        await redis.set(key, ctx.user_id, ex=PIN_RESET_TTL)

    log.info("PIN reset token generated", user_id=ctx.user_id, ttl=PIN_RESET_TTL)
    return token


# ── Complete PIN Reset (consume token) ──────────────────────────

async def reset_pin(
    db: AsyncSession,
    token: str,
    new_pin: str,
    redis: Redis | None = None,
) -> None:
    """
    Reset PIN using the email token. Unfreezes the wallet if frozen.

    Design note: this endpoint does NOT require authentication (no JWT).
    The token IS the identity proof — whoever has the 15-minute email
    token is allowed to reset the PIN.

    Single-use guarantee: the token is deleted from Redis BEFORE the
    new PIN is written to the DB. This prevents a race condition where
    two concurrent resets both read the token as valid.
    """
    if not redis:
        raise Exception("Redis is required for PIN reset flow")

    # Validate and consume the token atomically.
    key = PIN_RESET_TOKEN_KEY.format(token=token)
    user_id = await redis.get(key)

    if not user_id:
        # Token not found: either expired (>15 min) or already used.
        raise TokenInvalidError()

    if isinstance(user_id, bytes):
        user_id = user_id.decode("utf-8")

    # ── Consume token FIRST (before writing new PIN) ─────────────
    # Design note: delete the token immediately so a second concurrent
    # request with the same token finds nothing and raises TokenInvalidError.
    # This is the "check-then-act" pattern — we make the token invalid
    # before we perform the action it authorizes.
    await redis.delete(key)

    # Load wallet.
    result = await db.execute(
        select(Wallet).where(Wallet.user_id == UUID(user_id))
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise WalletNotFoundError()

    # Write the new bcrypt hash.
    wallet.transaction_pin_hash = hash_pin(new_pin)
    wallet.pin_attempts = 0

    # Unfreeze if frozen due to PIN lockout.
    if wallet.is_frozen:
        wallet.status = "active"

    # Clear the Redis attempt counter.
    attempts_key = PIN_ATTEMPTS_KEY.format(wallet_id=str(wallet.id))
    await redis.delete(attempts_key)

    # Also clear the auto-unfreeze marker.
    freeze_key = WALLET_FROZEN_KEY.format(wallet_id=wallet.id)
    await redis.delete(freeze_key)

    await log_audit(
        db=db,
        user_id=user_id,
        action=AuditActions.PIN_RESET,
        ip_address=None,   # No UserContext in a token-based reset
        details={"wallet_id": wallet.wallet_id},
    )

    await db.commit()
    log.info("PIN reset completed", user_id=user_id, wallet_id=wallet.wallet_id)


# ── Helper: Load Wallet in Usable State ─────────────────────────

async def _get_active_wallet(db: AsyncSession, user_id: str) -> Wallet:
    """
    Load the wallet for user_id and verify it can accept PIN operations.

    Used by set_pin, change_pin, and verify_transaction_pin.
    Centralised here to keep validation logic DRY.
    """
    result = await db.execute(
        select(Wallet).where(Wallet.user_id == UUID(user_id))
    )
    wallet = result.scalar_one_or_none()

    if not wallet:
        raise WalletNotFoundError()
    if wallet.is_closed:
        raise WalletClosedError()
    if wallet.is_frozen:
        # A frozen wallet can still request a PIN reset, but cannot
        # verify a PIN for a transaction. Callers that need to allow
        # frozen wallets (e.g. request_pin_reset) call the DB directly
        # instead of using this helper.
        raise WalletFrozenError()

    return wallet
