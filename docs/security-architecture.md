# Security Architecture

## Overview

VaultPay handles financial data and personally identifiable information (PII). Security is enforced at multiple layers:

1. **Network** — TLS, CORS, Nginx rate limiting
2. **Application** — JWT, RBAC, PIN verification, input validation
3. **Data** — AES-256 for KYC documents, bcrypt for PINs, environment-scoped secrets
4. **Audit** — Immutable audit log for all privileged operations

---

## Authentication Flow

```
                  ╔═════════════════════╗
                  ║     AuthShield      ║
                  ║                     ║
  [1] Login ──→   ║  Verify password    ║
                  ║  Check account      ║
                  ║  Issue JWT (HS256)  ║
                  ╚══════════╦══════════╝
                             ║
                    JWT delivered to client
                             ║
                  ╔══════════▼══════════╗
                  ║      VaultPay       ║
                  ║                     ║
  [2] Request ──→ ║  Decode JWT locally ║ ← shared JWT_SECRET_KEY
                  ║  Check is_active    ║
                  ║  Check not revoked  ║ ← Redis DB 0
                  ║  Enforce roles      ║
                  ╚═════════════════════╝
```

### Token Revocation

JWTs are stateless by default — once issued, they're valid until expiry. VaultPay handles immediate revocation:

- When a user **logs out** from AuthShield, the JWT's `jti` (JWT ID) is written to Redis with TTL = remaining token lifetime
- VaultPay's `get_current_user` dependency checks Redis for `as:tok:revoked:{jti}` on every request
- This adds ~1ms Redis lookup per request, enabling instant token invalidation

### Token Expiry Strategy

| Token Type | Expiry | Refresh |
|---|---|---|
| Access token | 15 minutes (default) | Silent refresh via refresh token |
| Refresh token | 7 days | Stored in HttpOnly cookie |

---

## PIN Security

PINs protect financial operations (send money, withdrawal):

- **Storage**: bcrypt-hashed (cost factor 12) — never stored in plain text
- **Verification**: bcrypt comparison on every sensitive operation
- **Lockout**: After 5 consecutive failures, wallet is locked for 24 hours (Redis counter)
- **Reset**: Via OTP sent to registered contact (AuthShield handles OTP generation and delivery)
- **Transmission**: Always over HTTPS; never logged

```python
# Example of what is NEVER acceptable:
# audit_logs.metadata = {"pin": user_provided_pin}  ❌

# Correct: log only that a PIN operation occurred
# audit_logs.action = "PIN_CHANGE" without any pin value  ✅
```

---

## KYC Document Encryption

Government ID document numbers fall under strict PII requirements:

```
User submits doc_number → AES-256-GCM encrypt → store ciphertext
                                    ↑
                          KYC_ENCRYPTION_KEY
                          from environment variable
                          (never in database)
```

When an admin views KYC:
```
ciphertext from DB → AES-256-GCM decrypt → show in API response → log access in audit_log
```

**Key rotation**: The `KYC_ENCRYPTION_KEY` must be rotated periodically. A separate migration script re-encrypts all existing records with the new key. Old key is retained in vault for the migration period only.

---

## RBAC Enforcement

See [system-architecture.md](./system-architecture.md#role-based-access-control-rbac) for the full permission matrix.

Security enforcement is **defense in depth**:

1. **Nginx**: Blocks unauthenticated requests from ever reaching application
2. **FastAPI dependency**: `require_roles(["admin"])` — raises 403 before handler runs
3. **Service layer**: Double-checks ownership (e.g. user can only access their own wallet)
4. **Database**: Row-level queries always include `WHERE user_id = :current_user_id` for user operations

---

## Input Validation

All request bodies are validated via Pydantic v2 models:

- **Amount fields**: `Decimal` with 2 decimal places, minimum `0.01`, maximum per transaction limit
- **PIN**: 4 digits only, regex `^\d{4}$`, never logged
- **Wallet ID**: Regex `^VPY-[A-Z0-9]{6}$`
- **Document number**: Regex per `doc_type` (e.g. Aadhaar is exactly 12 digits)
- **Description**: Max 255 characters, stripped of HTML

Pydantic raises `422 Unprocessable Entity` for invalid input before any business logic runs.

---

## Audit Log

The `audit_logs` table is the forensic record of all privileged actions:

**What is always logged:**
- Wallet freeze/unfreeze (by admin or self)
- KYC approval/rejection
- Transaction limit overrides
- User deactivation
- System settings changes
- Admin access to decrypted KYC documents

**Log entry includes:**
- `actor_id` + `actor_role`: who did it
- `action`: what was done (enum, not free text)
- `target_type` + `target_id`: what entity was affected
- `before_state` + `after_state`: JSONB snapshots for reconstructibility
- `request_id`: links to the HTTP request
- `ip_address`: source IP (from `X-Forwarded-For` via trusted proxy)
- `created_at`: UTC timestamp

**Database permissions:**
```sql
-- Application user has INSERT + SELECT only
GRANT SELECT, INSERT ON audit_logs TO vaultpay_app;
-- No UPDATE, no DELETE — ever
```

---

## API Rate Limiting

Two layers:

1. **Nginx** (network level): Global rate limit per IP — 100 req/min default
2. **Redis** (application level): Per-user, per-endpoint limits for financial operations

Financial endpoint limits:
- `POST /transactions/send`: 20 requests/minute per user
- `POST /transactions/topup`: 10 requests/minute per user
- `POST /pin/verify`: 5 attempts per 24 hours (enforced via lock key, not time window)

---

## Secrets Management

| Secret | Where Stored | Rotation |
|---|---|---|
| `JWT_SECRET_KEY` | Shared env var (both services) | Every 90 days |
| `DATABASE_URL` | Per-service env var | On credential rotation |
| `KYC_ENCRYPTION_KEY` | Per-service env var | With migration script |
| `REDIS_URL` | Per-service env var | On Redis password rotation |

In production: secrets are loaded from a vault (HashiCorp Vault or AWS Secrets Manager) at startup — never from `.env` files.

**`.env` files are for local development only** and are in `.gitignore`.

---

## CORS Configuration

VaultPay allows cross-origin requests only from explicitly whitelisted origins:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,  # e.g. ["https://app.vaultpay.in"]
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
```

`ALLOWED_ORIGINS` is empty by default — must be explicitly set per environment.
