# Security Architecture

> Full documentation coming in Phase 3.

This document will cover:
- Transaction PIN (bcrypt, 3-attempt lockout, 24h auto-freeze)
- KYC document encryption (AES-256-CBC, SHA-256 duplicate detection)
- IP Trust System (trusted/blocked IP tracking, email confirmation flow)
- Atomic P2P transfer safety (mid-freeze detection)
- Rate limiting strategy
