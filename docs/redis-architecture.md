# Redis Architecture

VaultPay uses Redis to complement PostgreSQL for high-speed, ephemeral data.

## Database Separation

Two Redis databases are used to prevent key collisions between services:

| DB | Service | Purpose |
|---|---|---|
| **Redis DB 0** | AuthShield | Session data, revoked tokens, login rate limiting, OTP codes |
| **Redis DB 1** | VaultPay | PIN attempt counters, notification counts, rate limiting |

The Redis connection URL scheme:
```
# AuthShield
REDIS_URL=redis://localhost:6379/0

# VaultPay
REDIS_URL=redis://localhost:6379/1
```

In production (Docker Compose), both services connect to the same Redis container but different logical databases — zero inter-service key interference.

---

## Key Schema

All keys follow a consistent pattern: `{service_prefix}:{entity_type}:{identifier}:{purpose}`

### VaultPay Keys (DB 1)

| Key | TTL | Purpose |
|---|---|---|
| `vp:pin:attempts:{wallet_id}` | 24h | Count of failed PIN attempts (max 5 before lock) |
| `vp:pin:locked:{wallet_id}` | 24h | Set to `"1"` when PIN is locked after 5 failures |
| `vp:notif:unread:{user_id}` | No TTL | Cached unread notification count (invalidated on read) |
| `vp:rate:topup:{user_id}` | 60s | Rate limit counter for top-up endpoint |
| `vp:rate:send:{user_id}` | 60s | Rate limit counter for send money endpoint |
| `vp:txn:daily:{wallet_id}:{action}:{date}` | 24h | Accumulated daily transaction total for limit checks |

### AuthShield Keys (DB 0)

| Key | TTL | Purpose |
|---|---|---|
| `as:tok:revoked:{jti}` | Token expiry | Revoked JWT identifiers (logout / account deactivation) |
| `as:otp:{user_id}:{purpose}` | 5min | OTP codes for PIN reset, 2FA |
| `as:rate:login:{ip}` | 60s | Failed login attempt counter per IP |
| `as:session:{session_id}` | 7d | Refresh token store |

---

## PIN Attempt Tracking

PIN verification is a critical path. Redis handles lockout safely:

```python
async def verify_pin_with_lock(wallet_id: UUID, pin: str, redis: Redis) -> bool:
    lock_key = f"vp:pin:locked:{wallet_id}"
    attempts_key = f"vp:pin:attempts:{wallet_id}"

    # Check if locked
    if await redis.exists(lock_key):
        raise PINLockedError()

    # Verify PIN against bcrypt hash in DB
    wallet = await db.get(Wallet, wallet_id)
    if not bcrypt.checkpw(pin.encode(), wallet.pin_hash.encode()):
        attempts = await redis.incr(attempts_key)
        await redis.expire(attempts_key, 86400)  # reset after 24h

        if attempts >= 5:
            await redis.setex(lock_key, 86400, "1")
            raise PINLockedError()

        raise InvalidPINError(attempts_remaining=5 - attempts)

    # Success: clear attempt counter
    await redis.delete(attempts_key)
    return True
```

---

## Rate Limiting

VaultPay uses a sliding window counter in Redis:

```python
async def check_rate_limit(key: str, limit: int, window_seconds: int, redis: Redis):
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window_seconds)
    if current > limit:
        raise RateLimitExceededError()
```

Default limits:
- Top-up: 10 requests per minute
- Send money: 20 requests per minute
- PIN verify: 5 attempts per 24 hours (enforced separately via lock key)

---

## Daily Limit Enforcement

To enforce daily transaction limits without a DB query on every transaction:

```python
async def check_daily_limit(wallet_id: UUID, action: str, amount: Decimal, redis: Redis, db):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    key = f"vp:txn:daily:{wallet_id}:{action}:{today}"

    # GET accumulated total from Redis
    accumulated = Decimal(await redis.get(key) or "0")

    limit = await get_wallet_limit(wallet_id, action, db)
    if accumulated + amount > limit.daily_limit:
        raise DailyLimitExceededError(
            limit=limit.daily_limit,
            accumulated=accumulated,
            requested=amount,
        )

    # After transaction commits, update counter
    await redis.incrbyfloat(key, float(amount))
    await redis.expireat(key, end_of_day_timestamp())
```

This avoids a `SUM(amount)` DB query on every transaction.
