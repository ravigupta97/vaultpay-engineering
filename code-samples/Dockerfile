# ── VaultPay Dockerfile ──────────────────────────────────────────
# Multi-stage build for production-ready container
#
# Stage 1: builder  — install deps in a venv
# Stage 2: runtime  — slim image with only runtime deps
#
# WHY MULTI-STAGE?
#   The builder stage includes compilers (gcc, libpq-dev) needed for C
#   extensions like asyncpg and bcrypt. The runtime stage copies only the
#   compiled virtualenv — no compiler toolchain in production image.
#   This reduces the final image size by ~60%.
#
# Usage:
#   docker build -t vaultpay .
#   docker run -p 8001:8001 --env-file .env vaultpay

# ═══════════════════════════════════════════════════════════════════
# Stage 1: Builder
# ═══════════════════════════════════════════════════════════════════
FROM python:3.12-slim AS builder

WORKDIR /app

# System deps for building C extensions (asyncpg, bcrypt, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Create virtualenv (keeps runtime image clean)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python deps (cache layer — only rebuilds when requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ═══════════════════════════════════════════════════════════════════
# Stage 2: Runtime
# ═══════════════════════════════════════════════════════════════════
FROM python:3.12-slim AS runtime

WORKDIR /app

# Runtime system deps only (no compiler)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY . .

# Make startup script executable
RUN chmod +x /app/scripts/start.sh

# Create non-root user for security
# Running as root in production containers is a security anti-pattern
RUN groupadd -r vaultpay && useradd -r -g vaultpay vaultpay
RUN chown -R vaultpay:vaultpay /app
USER vaultpay

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8

# Expose VaultPay port
EXPOSE 8001

# Health check — Docker marks container unhealthy if this fails 3x
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Startup: migrate → seed → run
# scripts/start.sh runs: alembic upgrade head && python scripts/seed_limits.py && uvicorn
CMD ["/app/scripts/start.sh"]
