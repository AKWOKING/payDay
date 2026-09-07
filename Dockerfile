# syntax=docker/dockerfile:1.7
###############################################################################
# PayDay e-Wallet — Backend Runtime Image
#
# Multi-stage build:
#   stage 1 (builder) — resolve and compile all Python dependencies into a
#                       self-contained virtualenv.
#   stage 2 (runtime) — copy only that virtualenv plus application source into
#                       a slim base, and run as an unprivileged user.
#
# The builder carries the compilers needed to build wheels (asyncpg, bcrypt,
# cryptography); the runtime image does not, which keeps the attack surface
# and the image size down.
###############################################################################

ARG PYTHON_VERSION=3.11

# --------------------------------------------------------------------------- #
# Stage 1 — builder
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build toolchain required to compile native extension wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create the virtualenv that will be handed to the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Copy only dependency metadata first so this layer caches independently of
# application source changes.
COPY pyproject.toml README.md ./

# `pip install .` needs the package tree to exist to resolve the build backend.
COPY src/ ./src/

RUN pip install --upgrade pip setuptools wheel \
    && pip install .

# --------------------------------------------------------------------------- #
# Stage 2 — runtime
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/opt/venv/bin:$PATH"

# Runtime-only shared libraries. `curl` backs the container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime account — the process must never run as root.
RUN groupadd --system --gid 1001 payday \
    && useradd --system --uid 1001 --gid payday --create-home payday

WORKDIR /app

# Bring across the fully-built virtualenv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Application source and migration tooling.
COPY --chown=payday:payday src/ ./src/
COPY --chown=payday:payday alembic/ ./alembic/
COPY --chown=payday:payday alembic.ini ./

# `src` layout: make the package importable without a re-install.
ENV PYTHONPATH=/app/src

USER payday

EXPOSE 8000

# Liveness probe hits the public health endpoint the API actually exposes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/api/v1/public/health || exit 1

CMD ["uvicorn", "payday.main:app", "--host", "0.0.0.0", "--port", "8000"]
