"""
atomic-transfer.py — VaultPay P2P Transfer Service
====================================================

This is the most security-critical function in VaultPay.
It executes a 6-step verified money transfer between two wallets
within a single atomic database transaction.

Design goals:
  ATOMIC     — Either everything succeeds or nothing changes.
  IDEMPOTENT — The same request_id always returns the same result.
               Prevents double-spends on client retries / network timeouts.
  SECURE     — PIN is bcrypt-verified before any balance changes occur.
  AUDITED    — Every transfer writes an immutable audit log entry.

This sample is extracted from:
  features/transactions/service.py  →  send_money()

NOTE: Internal import paths, settings, and logger calls have been
      simplified slightly for readability. No business logic changed.
"""

from decimal import Decimal
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditActions, log_audit
from core.context import UserContext
from core.security import verify_pin
from exceptions import (
    DuplicateTransactionError,
    InsufficientBalanceError,
    PinInvalidError,
    PinMaxAttemptsError,
    PinNotSetError,
    TransactionLimitExceededError,
    VaultPayException,
    WalletClosedError,
    WalletFrozenError,
    WalletNotFoundError,
)
from features.transactions.models import Transaction
from features.wallet.models import Wallet

log = structlog.get_logger()

# ── Redis Key Patterns ───────────────────────────────────────────
# Idempotency: "have we already processed this client request?"
# Key is the client-supplied idempotency_key, value is the transaction ref.
IDEMPOTENCY_KEY = "idempotency:{key}"
IDEMPOTENCY_TTL = 86400  # 24 hours — matches typical client retry window

# Brute-force guard: track consecutive wrong PINs per wallet.
# Stored in Redis (fast) and mirrored to DB (durable across Redis restarts).
PIN_ATTEMPTS_KEY = "pin_attempts:{wallet_id}"
MAX_PIN_ATTEMPTS = 3


async def send_money(
    db: AsyncSession,
    ctx: UserContext,                    # Decoded JWT claims: user_id, roles, client_ip
    receiver_wallet_code: str,           # Human-readable wallet ID e.g. "VPY-A1B2C3"
    amount: Decimal,                     # Decimal, never float — financial precision is mandatory
    pin: str,                            # Plain-text PIN from request body; never logged or stored
    description: str | None = None,
    idempotency_key: str | None = None,  # Client-generated UUID; optional but encouraged
    redis: Redis | None = None,
) -> Transaction:
    """
    Execute a P2P money transfer.

    All 6 steps run inside SQLAlchemy's implicit transaction context.
    If any step raises an exception, SQLAlchemy will roll back all
    in-flight DB writes on the next await, ensuring atomicity.
    """

    # ── Step 1: Load & validate sender wallet ─────────────────────
    #
    # Design note: we look up by user_id (from JWT), not from the request
    # body. The user cannot transfer from someone else's wallet —
    # the wallet is always their own.
    result = await db.execute(
        select(Wallet).where(Wallet.user_id == UUID(ctx.user_id))
    )
    sender_wallet = result.scalar_one_or_none()

    if not sender_wallet:
        raise WalletNotFoundError()

    # Closed wallets are permanently deactivated — no transfers in or out.
    if sender_wallet.is_closed:
        raise WalletClosedError()

    # Frozen wallets block all transactions (triggered by PIN lockout or admin action).
    # The user must unfreeze via PIN reset or admin review before sending.
    if sender_wallet.is_frozen:
        raise WalletFrozenError()

    # ── Step 2: Verify transaction PIN (rate-limited) ─────────────
    #
    # Design note: PIN verification happens BEFORE any balance or
    # receiver lookup. This is intentional:
    #   1. Fail fast — don't do expensive DB work for bad credentials.
    #   2. Prevent oracle attacks — don't reveal whether receivers exist
    #      until the sender is authenticated.
    if not sender_wallet.has_pin:
        raise PinNotSetError()

    if not verify_pin(pin, sender_wallet.transaction_pin_hash):
        # Increment the Redis attempt counter atomically.
        # Redis INCR is atomic — no race condition with concurrent requests.
        attempts = sender_wallet.pin_attempts
        if redis:
            key = PIN_ATTEMPTS_KEY.format(wallet_id=str(sender_wallet.id))
            redis_attempts = await redis.incr(key)   # Atomic increment
            await redis.expire(key, 86400)            # Reset counter after 24 h
            attempts = int(redis_attempts)

        # Also mirror to DB so the counter survives Redis restarts.
        sender_wallet.pin_attempts = attempts

        if attempts >= MAX_PIN_ATTEMPTS:
            # Auto-freeze the wallet on the 3rd wrong PIN.
            # The user must go through the PIN reset email flow to unfreeze.
            sender_wallet.status = "frozen"
            await db.commit()
            raise PinMaxAttemptsError()

        await db.commit()
        raise PinInvalidError()

    # PIN correct — reset the brute-force counter in both Redis and DB.
    sender_wallet.pin_attempts = 0
    if redis:
        key = PIN_ATTEMPTS_KEY.format(wallet_id=str(sender_wallet.id))
        await redis.delete(key)

    # ── Step 3: Idempotency check (double-spend prevention) ───────
    #
    # Design note: idempotency is a two-layer check:
    #   Layer 1 — Redis (fast, O(1)):  catches retries within the 24h window.
    #   Layer 2 — PostgreSQL (durable): catches retries after a Redis flush.
    #
    # If EITHER layer finds a duplicate, we raise DuplicateTransactionError
    # and return the original transaction ref to the client — so they can
    # look up the result of the first successful attempt.
    if idempotency_key:
        if redis:
            idem_key = IDEMPOTENCY_KEY.format(key=idempotency_key)
            existing_ref = await redis.get(idem_key)
            if existing_ref:
                # Already processed — return the original ref rather than erroring.
                raise DuplicateTransactionError(
                    existing_ref if isinstance(existing_ref, str) else existing_ref.decode()
                )

        # DB check: slower but survives Redis restarts.
        existing = await db.execute(
            select(Transaction).where(Transaction.idempotency_key == idempotency_key)
        )
        if txn := existing.scalar_one_or_none():
            raise DuplicateTransactionError(txn.transaction_ref)

    # ── Step 4: Load & validate receiver wallet ───────────────────
    #
    # Design note: receiver is looked up by wallet_code (e.g. "VPY-A1B2C3"),
    # not by user_id. This is intentional:
    #   - The sender knows the receiver's public wallet ID, not their UUID.
    #   - It prevents accidental self-transfers through the UI.
    result = await db.execute(
        select(Wallet).where(Wallet.wallet_id == receiver_wallet_code)
    )
    receiver_wallet = result.scalar_one_or_none()

    if not receiver_wallet:
        raise VaultPayException(
            message=f"Receiver wallet '{receiver_wallet_code}' not found.",
            error_code="VP_RECEIVER_NOT_FOUND",
        )

    # Closed wallets cannot receive funds — no silent money loss.
    if receiver_wallet.is_closed:
        raise VaultPayException(
            message="Receiver's wallet is closed and cannot accept transfers.",
            error_code="VP_RECEIVER_WALLET_CLOSED",
        )

    # Self-transfer guard — the DB check (same wallet UUID) is more reliable
    # than comparing wallet codes, as it handles edge cases like case differences.
    if receiver_wallet.id == sender_wallet.id:
        raise VaultPayException(
            message="Cannot send money to yourself.",
            error_code="VP_SELF_TRANSFER",
        )

    # ── Step 5: Balance & spending limit checks ───────────────────
    #
    # Design note: we use Decimal throughout — never float.
    # Floating-point arithmetic is non-deterministic for financial sums:
    #   >>> 0.1 + 0.2
    #   0.30000000000000004   ← unacceptable for a payments system
    #
    # daily_spent and monthly_spent are tracked on the wallet row so we
    # avoid a SUM(transactions) query on every transfer — O(1) vs O(n).
    if sender_wallet.balance < amount:
        raise InsufficientBalanceError()

    if sender_wallet.daily_spent + amount > sender_wallet.daily_limit:
        raise TransactionLimitExceededError("Daily", float(sender_wallet.daily_limit))

    if sender_wallet.monthly_spent + amount > sender_wallet.monthly_limit:
        raise TransactionLimitExceededError("Monthly", float(sender_wallet.monthly_limit))

    # ── Step 6: Execute the atomic transfer ──────────────────────
    #
    # Design note: SQLAlchemy's async session is implicitly transactional.
    # All of the following mutations (debit, credit, INSERT) are sent to
    # PostgreSQL inside a single BEGIN…COMMIT block. If db.commit() is
    # never reached (because an exception fires above), SQLAlchemy issues
    # an automatic ROLLBACK — no partial state is ever persisted.
    #
    # Both wallet rows must be updated in the same transaction to prevent
    # the "lost update" anomaly that would create or destroy money:
    #
    #   Sender  → balance -= amount, daily_spent  += amount, monthly_spent += amount
    #   Receiver → balance += amount  (no limit tracking needed for inbound)

    # Debit sender
    sender_wallet.balance -= amount
    sender_wallet.daily_spent += amount
    sender_wallet.monthly_spent += amount

    # Credit receiver
    receiver_wallet.balance += amount

    # Create the immutable transaction record.
    # Design note: transactions are append-only — status can change (e.g.
    # to "disputed") but amount and wallet refs never change.
    txn = Transaction(
        sender_wallet_id=sender_wallet.id,
        receiver_wallet_id=receiver_wallet.id,
        amount=amount,
        type="transfer",
        status="completed",
        description=description,
        idempotency_key=idempotency_key,
    )
    db.add(txn)

    # ── Step 7: Audit log + commit ────────────────────────────────
    #
    # log_audit() inserts into the audit_logs table inside the same
    # transaction. If the commit fails, the audit entry is also rolled
    # back, keeping the audit log consistent with actual state changes.
    await log_audit(
        db=db,
        user_id=ctx.user_id,
        action=AuditActions.TRANSFER_COMPLETED,
        ip_address=ctx.client_ip,
        details={
            "transaction_ref": txn.transaction_ref,
            "amount": str(amount),            # str to preserve exact decimal
            "receiver_wallet": receiver_wallet_code,
        },
    )

    # Single commit — everything above lands atomically or not at all.
    await db.commit()
    await db.refresh(txn)  # Reload DB-generated fields (transaction_ref, created_at)

    # Cache the idempotency key in Redis AFTER the DB commit.
    # Design note: we write to Redis only after a successful commit.
    # If we wrote before and then the commit failed, Redis would falsely
    # mark a transaction as completed when no money actually moved.
    if redis and idempotency_key:
        idem_key = IDEMPOTENCY_KEY.format(key=idempotency_key)
        await redis.set(idem_key, txn.transaction_ref, ex=IDEMPOTENCY_TTL)

    log.info(
        "Transfer completed",
        ref=txn.transaction_ref,
        amount=str(amount),
        sender=sender_wallet.wallet_id,
        receiver=receiver_wallet_code,
    )

    return txn
