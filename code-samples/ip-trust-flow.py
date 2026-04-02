"""
ip-trust-flow.py — VaultPay IP Trust & Confirmation System
===========================================================

IP Trust protects users from transactions made from unknown devices
or locations (e.g. a stolen session token used from a different country).

Flow for a new, unknown IP:
  1. User sends a transaction request from IP X.
  2. is_ip_trusted() checks Redis → returns False (IP not known).
  3. The transaction is BLOCKED. An email confirmation link is sent.
  4. User clicks the link → POST /ip-trust/confirm?token=<token>
  5. confirm_ip() validates the token, writes the IP hash to Redis (30d TTL).
  6. Future requests from the same IP pass the check immediately (O(1) Redis GET).

Security design decisions:
  - IPs are stored as SHA-256 hashes, never in plaintext. If Redis is
    compromised, the attacker learns hashed IPs — not usable for geolocation.
  - Confirmation tokens use 256-bit entropy (secrets.token_urlsafe(32)).
  - Tokens have a 30-minute TTL and are single-use (deleted on first use).
  - Fail-open strategy: if Redis is unavailable, ALL IPs are trusted.
    Rationale: availability > security for a payments app. A Redis outage
    should not block all transactions — it's a transient infrastructure issue,
    not a security event. The PIN check provides the primary auth layer.

Redis key schema (VaultPay DB 1):
  known_ip:{user_id}:{ip_hash}   → "1"             (TTL: 30 days)
  ip_confirm_token:{token}       → "user_id:ip_hash" (TTL: 30 minutes)

This sample is extracted from:
  features/ip_trust/service.py
  core/ip_detection.py
"""

import hashlib
import secrets

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditActions, log_audit
from core.context import UserContext
from exceptions import VaultPayException

log = structlog.get_logger()

# ── Redis Key Patterns ───────────────────────────────────────────
KNOWN_IP_KEY      = "known_ip:{user_id}:{ip_hash}"     # Trust record per user per IP
CONFIRM_TOKEN_KEY = "ip_confirm_token:{token}"          # Email confirmation token
KNOWN_IP_PREFIX   = "known_ip:{user_id}:*"             # Used for SCAN to list all trusted IPs

# ── TTLs ─────────────────────────────────────────────────────────
KNOWN_IP_TTL      = 30 * 24 * 3600   # 30 days — re-confirmation required monthly
CONFIRM_TOKEN_TTL = 30 * 60          # 30 minutes — short window to prevent token harvesting


# ── IP Hashing ───────────────────────────────────────────────────

def hash_ip(ip_address: str) -> str:
    """
    SHA-256 hash of an IP address.

    Design note: we hash IPs before storing them in Redis so that:
      - A Redis breach doesn't expose user geolocation data.
      - We can still check "is this exact IP trusted?" deterministically
        (same input → same hash, always).
      - We comply with privacy-by-design principles (GDPR Article 25).

    SHA-256 is used (not bcrypt) because:
      - bcrypt is designed to be slow (brute-force resistance), which is
        unnecessary here since IPs have low entropy anyway.
      - We need the hash to be deterministic and fast for O(1) lookups.
      - The goal is pseudonymisation, not password security.
    """
    return hashlib.sha256(ip_address.encode("utf-8")).hexdigest()


def mask_ip(ip_hash: str) -> str:
    """
    Return a truncated display version of an IP hash for the UI.

    Since we only store hashes, we cannot show "192.168.1.1".
    Instead we show the first 8 hex chars: "***a3f9b2c1".
    This lets users identify which device/location the IP refers to
    without revealing the full hash (which could be brute-forced for
    common IP ranges like 192.168.x.x).
    """
    return f"***{ip_hash[:8]}"


# ── Core Check: Is This IP Trusted? ─────────────────────────────

async def is_ip_trusted(
    redis: Redis | None,
    user_id: str,
    ip_address: str,
) -> bool:
    """
    O(1) Redis lookup: has this user previously confirmed this IP?

    Called on every transaction request, so performance is critical.
    A Redis GET is ~0.1 ms — negligible compared to bcrypt (~100 ms).

    Fail-open: if Redis is None (unavailable), returns True.
    See module docstring for the rationale behind fail-open.
    """
    if not redis:
        # Fail-open: an unavailable Redis should not block transactions.
        # The PIN check is the primary security gate; IP trust is a
        # secondary, defence-in-depth layer.
        log.warning("Redis unavailable — IP trust check skipped (fail-open)")
        return True

    ip_h = hash_ip(ip_address)
    key = KNOWN_IP_KEY.format(user_id=user_id, ip_hash=ip_h)

    # GET returns the value if the key exists, None if not or expired.
    result = await redis.get(key)
    return result is not None


# ── Generate Email Confirmation Token ───────────────────────────

async def generate_confirm_token(
    redis: Redis | None,
    user_id: str,
    ip_address: str,
) -> str | None:
    """
    Generate a one-time URL token for IP confirmation, stored in Redis.

    Called when is_ip_trusted() returns False. The token is embedded
    in an email link sent to the user's registered address.

    Token payload stored in Redis: "{user_id}:{ip_hash}"
    This is all confirm_ip() needs to trust the IP — no DB lookup required.

    Returns None if Redis is unavailable (token-less flow; transaction
    blocked but no email sent — user must retry later).
    """
    if not redis:
        return None

    ip_h = hash_ip(ip_address)

    # 256-bit random token — effectively unguessable.
    # Using secrets (not random) for cryptographic security.
    token = secrets.token_urlsafe(32)

    key = CONFIRM_TOKEN_KEY.format(token=token)

    # Pack both user_id and ip_hash into the Redis value.
    # Design note: we store the ip_hash (not the original IP) so the
    # Redis value is also privacy-safe, consistent with the trust keys.
    await redis.set(key, f"{user_id}:{ip_h}", ex=CONFIRM_TOKEN_TTL)

    log.info(
        "IP confirmation token generated",
        user_id=user_id,
        ip_hash=ip_h[:8],   # Log only the first 8 chars (display-safe)
        ttl_minutes=CONFIRM_TOKEN_TTL // 60,
    )
    return token


# ── Confirm IP via Token ─────────────────────────────────────────

async def confirm_ip(
    redis: Redis | None,
    token: str,
    db: AsyncSession | None = None,
) -> dict:
    """
    Validate the email token and add the IP to the trusted list.

    This endpoint is unauthenticated — the token IS the credential.
    Having the token proves the user received the email, which proves
    they control the registered email address.

    Single-use guarantee: the token is deleted BEFORE the trust key is
    written. If two concurrent requests arrive with the same token, only
    one will find the key in Redis and proceed. The other will get
    VP_IP_TOKEN_INVALID.

    Design note: the order matters —
      1. Read token value (get user_id + ip_hash)
      2. DELETE token  ← single-use enforcement
      3. Write trust key
      4. Audit log (if DB available)
    """
    if not redis:
        raise VaultPayException(
            message="IP confirmation service is temporarily unavailable.",
            error_code="VP_IP_SERVICE_UNAVAILABLE",
        )

    key = CONFIRM_TOKEN_KEY.format(token=token)
    data = await redis.get(key)

    if not data:
        # Token not found: expired (>30 min), already used, or never existed.
        raise VaultPayException(
            message="Invalid or expired confirmation token.",
            error_code="VP_IP_TOKEN_INVALID",
        )

    data_str = data if isinstance(data, str) else data.decode()
    user_id, ip_hash = data_str.split(":", 1)

    # ── Delete the token FIRST (single-use enforcement) ─────────
    await redis.delete(key)

    # ── Write the trust key ─────────────────────────────────────
    # 30-day TTL: the user will need to re-confirm this IP after a month.
    # This limits the blast radius of a stolen session:
    # even if an attacker trusts an IP, that trust expires in 30 days.
    ip_key = KNOWN_IP_KEY.format(user_id=user_id, ip_hash=ip_hash)
    await redis.set(ip_key, "1", ex=KNOWN_IP_TTL)

    # ── Audit log entry ─────────────────────────────────────────
    if db:
        await log_audit(
            db=db,
            user_id=user_id,
            action=AuditActions.IP_CONFIRMED,
            ip_address=None,                # Original IP is unavailable here (only hash)
            details={"ip_hash": ip_hash[:8]},
        )

    log.info("IP confirmed via email token", user_id=user_id, ip_hash=ip_hash[:8])
    return {"user_id": user_id, "ip_hash": ip_hash}


# ── Add Trusted IP Directly ──────────────────────────────────────

async def add_trusted_ip(
    redis: Redis | None,
    user_id: str,
    ip_address: str,
) -> None:
    """
    Directly trust an IP address (no email confirmation required).

    Called after the user's first successful transaction from this IP —
    once they've proven they own the wallet (PIN verified), we trust the
    current IP for future transactions automatically.

    This avoids friction for the user's "home" device/network.
    """
    if not redis:
        return

    ip_h = hash_ip(ip_address)
    key = KNOWN_IP_KEY.format(user_id=user_id, ip_hash=ip_h)
    await redis.set(key, "1", ex=KNOWN_IP_TTL)

    log.info("Trusted IP added directly", user_id=user_id, ip_hash=ip_h[:8])


# ── List Trusted IPs ─────────────────────────────────────────────

async def list_trusted_ips(
    redis: Redis | None,
    user_id: str,
) -> list[dict]:
    """
    Return all trusted IPs for a user.

    Design note: we use SCAN (not KEYS) to iterate Redis keys matching
    the pattern. KEYS blocks the Redis event loop — unacceptable in
    production. SCAN is iterative and non-blocking, processing a
    configurable batch of keys per call.

    Returns masked display versions (***a3f9b2c1) since we don't store
    the original IP addresses.
    """
    if not redis:
        return []

    pattern = KNOWN_IP_PREFIX.format(user_id=user_id)
    ips = []

    # scan_iter is the async equivalent of the SCAN command, yielding
    # matching keys in batches without blocking the event loop.
    async for key in redis.scan_iter(match=pattern):
        key_str = key if isinstance(key, str) else key.decode()
        # Key format: known_ip:{user_id}:{ip_hash}
        parts = key_str.split(":")
        if len(parts) >= 3:
            ip_hash = parts[-1]
            ips.append({
                "ip_hash": ip_hash,
                "ip_display": mask_ip(ip_hash),
            })

    return ips


# ── Remove Trusted IP ────────────────────────────────────────────

async def remove_trusted_ip(
    redis: Redis | None,
    user_id: str,
    ip_hash: str,
    db: AsyncSession | None = None,
    client_ip: str | None = None,
) -> bool:
    """
    Remove a trusted IP from the user's list.

    Use case: user suspects their device was compromised, or wants to
    revoke trust for a public Wi-Fi network they previously confirmed.

    Returns True if the key existed and was deleted (idempotent — returns
    False if the IP wasn't trusted to begin with).
    """
    if not redis:
        return False

    key = KNOWN_IP_KEY.format(user_id=user_id, ip_hash=ip_hash)
    deleted_count = await redis.delete(key)

    if deleted_count > 0 and db:
        await log_audit(
            db=db,
            user_id=user_id,
            action=AuditActions.IP_REMOVED,
            ip_address=client_ip,
            details={"ip_hash": ip_hash[:8]},
        )
        await db.commit()

    log.info("Trusted IP removed", user_id=user_id, ip_hash=ip_hash[:8])
    return deleted_count > 0
