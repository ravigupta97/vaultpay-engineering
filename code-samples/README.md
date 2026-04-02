# Code Samples

Annotated, production-quality extracts from the private VaultPay repository.

Each file includes inline comments explaining **why** the pattern was chosen — not just what the code does.

## Architecture & Infrastructure

| File | What It Shows |
|---|---|
| [exception-hierarchy.py](exception-hierarchy.py) | 20+ typed exceptions with error codes, organized by HTTP status. All caught in `main.py` and converted to standard JSON responses. |
| [common-schemas.py](common-schemas.py) | `StandardResponse[T]`, `PaginatedResponse`, `PaginationParams` — the generic response envelope used by every endpoint. |
| [wallet-model.py](wallet-model.py) | SQLAlchemy 2.0 async model for financial data. Shows `Numeric` over `Float`, cross-service `user_id` (no FK), and status state machine. |
| [auth-middleware.py](auth-middleware.py) | Starlette middleware that assigns/propagates `X-Request-ID` for distributed tracing across VaultPay ↔ AuthShield. |
| [Dockerfile](Dockerfile) | Multi-stage production build (builder → runtime). Reduces final image size by ~60% and runs as non-root user. |
| [docker-compose.yml](docker-compose.yml) | Full stack orchestration: VaultPay app + PostgreSQL 16 + Redis 7, with healthchecks and explicit `depends_on`. |

## Core Business Logic

| File | What It Shows |
|---|---|
| [atomic-transfer.py](atomic-transfer.py) | The 6-step P2P money transfer: PIN verification → idempotency check (Redis + DB dual-layer) → balance/limit validation → atomic debit+credit → audit log. Every design decision annotated inline. |
| [pin-lockout.py](pin-lockout.py) | Transaction PIN brute-force protection. Redis INCR for atomic attempt counting, dual-write to DB for durability, wallet auto-freeze on 3rd failure, and email-token-based PIN reset with single-use enforcement. |
| [ip-trust-flow.py](ip-trust-flow.py) | IP trust detection using SHA-256-hashed IPs in Redis. 30-minute confirmation tokens, fail-open strategy when Redis is unavailable, and SCAN-based trusted IP listing without blocking the event loop. |
