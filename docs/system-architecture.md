# System Architecture

> Full documentation coming in Phase 2.

This document will cover:
- Microservice topology (VaultPay + AuthShield)
- JWT dual-mode authentication (fast-path vs strict-path)
- 4-tier RBAC (`user → moderator → admin → super_admin`)
- Request flow and X-Request-ID tracing
- Service-to-service communication
