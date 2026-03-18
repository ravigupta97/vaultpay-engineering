# Database Design

## Schema Overview

VaultPay's PostgreSQL schema consists of **8 tables**.

| Table | Purpose |
|---|---|
| `wallets` | One per user. Holds balance and wallet state. |
| `transactions` | Every credit and debit event. Append-only. |
| `transaction_limits` | Per-user, per-action configurable limits. |
| `kyc_submissions` | KYC documents and verification status. |
| `disputes` | Dispute claims against completed transactions. |
| `audit_logs` | Immutable log of all admin and system actions. |
| `notifications` | In-app notification inbox per user. |
| `system_settings` | Key-value store for runtime config (e.g. default limits). |

---

## Entity Relationship Diagram

```mermaid
erDiagram
    wallets {
        UUID id PK
        VARCHAR wallet_id UK "VPY-XXXXXX"
        UUID user_id UK "AuthShield user, no FK"
        NUMERIC balance "DECIMAL(12,2)"
        VARCHAR currency "ISO 4217"
        VARCHAR status "active|frozen|closed"
        VARCHAR pin_hash "bcrypt"
        INT pin_attempts
        TIMESTAMP created_at
        TIMESTAMP updated_at
    }

    transactions {
        UUID id PK
        UUID wallet_id FK
        VARCHAR reference_id UK "VP-TXN-XXXXXX"
        VARCHAR type "credit|debit"
        NUMERIC amount "DECIMAL(12,2)"
        VARCHAR currency
        VARCHAR category "top_up|p2p_send|p2p_receive|withdrawal|refund"
        VARCHAR status "pending|completed|failed|reversed"
        UUID related_wallet_id "nullable - receiver or sender"
        TEXT description
        JSONB metadata
        UUID initiated_by "user_id from JWT"
        VARCHAR request_id "X-Request-ID for tracing"
        TIMESTAMP created_at
        TIMESTAMP updated_at
    }

    transaction_limits {
        UUID id PK
        UUID wallet_id FK
        VARCHAR action "send_money|top_up|withdrawal"
        NUMERIC daily_limit "DECIMAL(12,2)"
        NUMERIC per_transaction_limit "DECIMAL(12,2)"
        NUMERIC monthly_limit "DECIMAL(12,2), nullable"
        TIMESTAMP created_at
        TIMESTAMP updated_at
    }

    kyc_submissions {
        UUID id PK
        UUID wallet_id FK UK
        VARCHAR doc_type "aadhar|pan|passport|voters_id"
        TEXT doc_number_encrypted "AES-256 encrypted"
        VARCHAR status "pending|verified|rejected"
        TEXT rejection_reason "nullable"
        UUID reviewed_by "admin user_id, nullable"
        TIMESTAMP submitted_at
        TIMESTAMP reviewed_at "nullable"
    }

    disputes {
        UUID id PK
        UUID wallet_id FK
        UUID transaction_id FK
        VARCHAR status "open|under_review|resolved|rejected"
        TEXT reason
        TEXT resolution_notes "nullable"
        UUID resolved_by "admin user_id, nullable"
        TIMESTAMP created_at
        TIMESTAMP resolved_at "nullable"
    }

    audit_logs {
        UUID id PK
        UUID actor_id "user who triggered the action"
        VARCHAR actor_role
        VARCHAR action "FREEZE_WALLET|APPROVE_KYC|etc"
        VARCHAR target_type "wallet|transaction|kyc|user"
        UUID target_id
        JSONB before_state "nullable snapshot"
        JSONB after_state "nullable snapshot"
        VARCHAR request_id
        VARCHAR ip_address
        TIMESTAMP created_at
    }

    notifications {
        UUID id PK
        UUID user_id "AuthShield user, no FK"
        VARCHAR type "transaction|kyc|dispute|system"
        VARCHAR title
        TEXT body
        BOOLEAN is_read
        JSONB metadata "nullable"
        TIMESTAMP created_at
    }

    system_settings {
        UUID id PK
        VARCHAR key UK "e.g. default_daily_send_limit"
        TEXT value
        TEXT description
        TIMESTAMP updated_at
    }

    wallets ||--o{ transactions : "has many"
    wallets ||--o{ transaction_limits : "one per action type"
    wallets ||--o| kyc_submissions : "has one"
    wallets ||--o{ disputes : "has many"
    transactions ||--o{ disputes : "referenced in"
```

---

## Key Design Decisions

### 1. `DECIMAL(12, 2)` Not `FLOAT` for Money
All amount columns use PostgreSQL `NUMERIC(12, 2)`:
- `FLOAT` uses IEEE 754 binary representation — cannot represent 0.1 exactly
- `NUMERIC` is exact — no rounding errors in summation or comparison
- Python's `Decimal` type maps directly; SQLAlchemy uses `Numeric(precision=12, scale=2)`

```python
# Correct
balance: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2))

# NEVER do this for money
balance: Mapped[float] = mapped_column(Float)  # ❌ rounding errors guaranteed
```

### 2. `user_id` Has No Foreign Key Constraint

`wallets.user_id`, `notifications.user_id`, and `audit_logs.actor_id` reference AuthShield's `users` table — which lives in a **different PostgreSQL database**.

A DB-level FK constraint would fail at migration time. Instead:
- Application layer enforces referential integrity (JWT validation ensures user exists)
- `user_id` has a `UNIQUE` constraint and index for fast lookups
- If AuthShield's user is deleted, a cleanup job (or soft-delete strategy) handles orphan wallets

### 3. Transactions Are Append-Only

The `transactions` table is **never updated** after the row is created:
- Financial ledgers are audit trails — mutations would hide history
- `status` can change (`pending → completed`, `completed → reversed`) but the original row stays
- Reversal creates a **new** transaction of type `refund` with a reference to the original

### 4. Encrypted KYC Data

`kyc_submissions.doc_number_encrypted` stores the document number as AES-256 ciphertext:
- Key stored in `settings.KYC_ENCRYPTION_KEY` (never in DB)
- Decrypted only when an admin views a specific submission
- Rejection during KYC does **not** delete the encrypted data (audit trail requirement)

### 5. `JSONB` for Flexible Metadata

`transactions.metadata` and `audit_logs.before_state` / `after_state` use `JSONB`:
- Allows schema-free extensibility (e.g. UPI reference ID for top-ups, bank code for withdrawals)
- PostgreSQL `JSONB` is stored binary — faster querying than `JSON`
- `JSONB` supports GIN indexing for `@>` containment queries

### 6. Audit Log Is Immutable

`audit_logs` has no `UPDATE` or `DELETE` permissions granted to the application user:
- Only `INSERT` and `SELECT` allowed
- `before_state` / `after_state` capture a snapshot of the row before and after each admin action
- Enables full reconstruction of wallet state at any point in time

---

## Indexes

| Table | Column | Index Type | Why |
|---|---|---|---|
| `wallets` | `wallet_id` | UNIQUE B-tree | P2P lookup by human-readable ID |
| `wallets` | `user_id` | UNIQUE B-tree | One wallet per user check |
| `transactions` | `wallet_id` | B-tree | Fetch wallet's transaction history |
| `transactions` | `reference_id` | UNIQUE B-tree | Idempotency check |
| `transactions` | `created_at` | B-tree | Date-range queries |
| `kyc_submissions` | `wallet_id` | UNIQUE B-tree | One KYC per wallet |
| `disputes` | `wallet_id` | B-tree | List disputes for a wallet |
| `disputes` | `transaction_id` | B-tree | Find dispute for a transaction |
| `notifications` | `user_id, is_read` | B-tree (composite) | Unread notification count |
| `audit_logs` | `target_id, target_type` | B-tree (composite) | Audit history for any entity |
| `system_settings` | `key` | UNIQUE B-tree | O(1) settings lookup |
