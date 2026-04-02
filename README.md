<div align="center">

# 🏦 VaultPay Engineering

**A production-grade digital wallet backend built with FastAPI, PostgreSQL, and Redis.**

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis 7](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)](https://redis.io/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00)](https://www.sqlalchemy.org/)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-E92063)](https://docs.pydantic.dev/)

</div>

---

## What Is This Repository?

This is the **public engineering documentation** for VaultPay — a fintech microservice handling wallets, peer-to-peer transfers, KYC verification, and transaction security. The full private codebase is not public, but this repository documents the architecture, design decisions, and engineering patterns in depth.

> **Who is this for?** Engineers evaluating the codebase, recruiters reviewing technical depth, or anyone curious about how to build a production-grade financial API with FastAPI.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Client / Frontend                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
            ┌──────────────┴──────────────┐
            │                             │
    ┌───────▼────────┐           ┌────────▼────────┐
    │  AuthShield    │           │    VaultPay      │
    │  (Port 8000)   │           │  (Port 8001)     │
    │                │◄──────────│                  │
    │  Auth & Users  │  JWT      │  Wallets, Txns   │
    │  JWT issuance  │  verify   │  KYC, IP Trust   │
    └───────┬────────┘           └────────┬─────────┘
            │                             │
    ┌───────▼────────┐           ┌────────▼─────────┐
    │   Redis DB 0   │           │   Redis DB 1      │
    │  (AuthShield)  │           │  (VaultPay)       │
    └───────┬────────┘           └──────────────────┘
            │                             │
    ┌───────▼────────┐           ┌────────▼─────────┐
    │  AuthShield DB │           │  VaultPay DB      │
    │  (PostgreSQL)  │           │  (PostgreSQL 16)  │
    └────────────────┘           └──────────────────┘
```

VaultPay and AuthShield are **two separate microservices with separate databases**. VaultPay validates JWTs locally (fast path) or calls AuthShield's `/auth/me` endpoint (strict path) — never accessing the AuthShield database directly.

---

## Feature Highlights

| Feature | Details |
|---|---|
| 🔐 **Transaction PIN** | bcrypt-hashed 4-digit PIN, 3-attempt lockout, 24h auto-unfreeze |
| 💳 **Wallet System** | One wallet per user, `VPY-XXXXXX` human-readable IDs, status state machine |
| 💸 **P2P Transfers** | 6-step atomic transfers, mid-freeze detection, idempotency keys |
| 🛡️ **IP Trust System** | Per-IP tracking, 30-min block on new IPs, email confirmation flow |
| 📋 **KYC Verification** | AES-256 encrypted document storage, SHA-256 hash for duplicate detection |
| 📊 **Transaction Limits** | Daily/monthly spend limits, elevated after KYC approval |
| 👮 **4-Tier RBAC** | `user → moderator → admin → super_admin` across AuthShield + VaultPay |
| 📝 **Audit Logging** | Append-only audit trail for all privileged operations |

---

## Tech Stack

| Layer | Technology |
|---|---|
| **API Framework** | FastAPI 0.115 + Uvicorn |
| **Database** | PostgreSQL 16 (asyncpg driver) |
| **ORM** | SQLAlchemy 2.0 (async) + Alembic migrations |
| **Caching / State** | Redis 7 (two separate DBs) |
| **Auth** | JWT (HS256), dual-mode validation |
| **Validation** | Pydantic v2 |
| **Security** | bcrypt (PIN), AES-256 (KYC), rate limiting |
| **Containerization** | Docker + Docker Compose |
| **Testing** | pytest + pytest-asyncio |
| **Auth Microservice** | AuthShield (companion service) |

---

## Project Structure (Private Repo)

```
vaultpay/
├── features/
│   ├── wallet/          # Wallet create, balance, status management
│   ├── pin/             # Transaction PIN set/verify/change
│   ├── transactions/    # Send money, history, disputes
│   ├── kyc/             # KYC document submission & review
│   ├── notifications/   # In-app notification management
│   ├── ip_trust/        # IP address trust management
│   ├── admin/           # Moderator + admin operations
│   └── superadmin/      # Super admin system management
├── core/
│   ├── authshield.py    # AuthShield client (JWT validation, user lookup)
│   ├── security.py      # bcrypt, AES-256, rate limiting utilities
│   └── audit.py         # Audit log helper
├── middleware/
│   └── request_id.py    # X-Request-ID propagation
├── schemas/
│   └── common.py        # StandardResponse[T], PaginatedResponse
├── exceptions.py        # Centralized exception hierarchy
├── dependencies.py      # FastAPI dependency injection
├── database.py          # Async SQLAlchemy session factory
├── redis_client.py      # Redis connection pool
├── main.py              # App factory, middleware, routers
├── Dockerfile           # Multi-stage production build
└── docker-compose.yml   # Full stack orchestration
```

---

## Documentation

| Document | Description |
|---|---|
| [System Architecture](docs/system-architecture.md) | Microservice design, JWT dual-mode auth, RBAC |
| [Database Design](docs/database-design.md) | ERD, all 8 tables, schema rationale |
| [API Reference](docs/api-reference.md) | All 48+ endpoints across 8 feature groups |
| [Security Architecture](docs/security-architecture.md) | PIN, KYC encryption, IP trust, atomic transfers |
| [Redis Architecture](docs/redis-architecture.md) | Key patterns, DB separation, TTL strategy |
| [Feature Flows](docs/feature-flows.md) | Send money, KYC, dispute flows with sequence diagrams |

---

## Code Samples

Annotated, production-ready code samples from the private repo. Every file includes inline comments explaining **why** each design decision was made.

**Architecture & Infrastructure**

| File | Pattern Demonstrated |
|---|---|
| [exception-hierarchy.py](code-samples/exception-hierarchy.py) | Centralized typed exception system with HTTP status mapping |
| [wallet-model.py](code-samples/wallet-model.py) | SQLAlchemy 2.0 financial data modeling (`Numeric` over `Float`, state machine) |
| [auth-middleware.py](code-samples/auth-middleware.py) | Request ID propagation middleware for distributed tracing |
| [common-schemas.py](code-samples/common-schemas.py) | Generic `StandardResponse[T]` API response envelope |
| [docker-compose.yml](code-samples/docker-compose.yml) | Multi-service container orchestration with healthchecks |
| [Dockerfile](code-samples/Dockerfile) | Multi-stage production build (builder → runtime, non-root user) |

**Core Business Logic**

| File | Pattern Demonstrated |
|---|---|
| [atomic-transfer.py](code-samples/atomic-transfer.py) | 6-step P2P transfer: PIN auth → dual-layer idempotency → atomic debit+credit → audit log |
| [pin-lockout.py](code-samples/pin-lockout.py) | Brute-force PIN protection: Redis atomic INCR, dual-write durability, auto-freeze, email reset |
| [ip-trust-flow.py](code-samples/ip-trust-flow.py) | IP trust detection: SHA-256 hashing, 30-min confirmation tokens, fail-open Redis strategy |

---

## Roadmap (v2)

- **RAG-based KYC** — LLM-assisted document extraction and verification
- **Geo-level IP Detection** — Country/city-level IP change detection with policy rules
- **Multi-currency Support** — INR → multi-currency with real-time FX rates
- **WebSocket Notifications** — Real-time transaction alerts
- **Partial Transaction Reversal** — Admin-initiated partial refunds for disputes

---

<div align="center">

Built with precision. Designed for production.

</div>
