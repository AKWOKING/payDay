# PayDay — Final Project Handover

**Version:** 1.0 · **Date:** 2026-09-07
**Repository:** `AKWOKING/payDay`
**Status:** Sprints 1–4 complete · 101 automated tests (100 passing, 1 skipped) · **Pilot readiness: AMBER**

---

## 1. What This Document Is

The operations runbook and architectural reference for the PayDay backend. It
describes the system **as it actually stands today**, including what is not
finished. Section 9 (Known Gaps) is the part to read before any production
decision.

---

## 2. System Overview

PayDay is a neutral electronic wallet for Cameroon (CEMAC / XAF) implementing the
**Central Triangle Model**: one central digital wallet bridging **MTN Mobile
Money**, **Orange Money**, and **UBA Bank**, so value moves between otherwise
non-interoperable channels.

```
   MTN MoMo ─────┐
                 ├──► PayDay Central Wallet ──► Double-entry ledger
   Orange Money ─┤         (XAF)                Pessimistic row locking
                 │                              AES-256-GCM PII at rest
   UBA Bank ─────┘  (Phase 2)
```

**Clients:** Flutter mobile (customers), Angular landing page (marketing + fee
simulator), Angular admin back-office (KYC, reconciliation, freezes, reversals).

---

## 3. Technology

| Layer | Technology | Status |
| --- | --- | --- |
| Runtime | Python 3.11+ | Active |
| Framework | FastAPI (async) | Active |
| ORM | SQLAlchemy 2.0 (AsyncIO) | Active |
| Database | PostgreSQL 15 (`asyncpg`); SQLite for local/test | Active |
| Migrations | Alembic | Active |
| Auth | JWT bearer (`python-jose`), bcrypt password + PIN | Active |
| Encryption | AES-256-GCM for PII at rest | Active |
| API contract | OpenAPI 3.1, RFC 7807 errors | Active |
| Container | Multi-stage Docker, non-root | Built, unbuilt in CI so far |
| **Redis 7** | **Specified, NOT wired** | **See §9.2** |
| **Celery** | **Specified, NOT wired** | **See §9.2** |

---

## 4. Repository Layout

```
src/payday/
  main.py              FastAPI app, CORS, RFC 7807 handlers, router mount
  core/                config, database, security, encryption, exceptions, logging
  models/              SQLAlchemy models (user, wallet, transaction, …)
  schemas/             Pydantic request/response contracts
  services/            wallet_engine, transaction_manager, reconciliation,
                       kyc, auth, audit, notification, task_queue
  adapters/            base (port), mtn_momo, orange_money, factory
  api/v1/              auth, kyc, wallet, transactions, notifications,
                       webhooks, mock_telco, admin, public
alembic/versions/      001_initial_schema, 002_fix_schema_drift
tests/                 101 tests across sprints 1–4
docs/                  reports, this handover, CI/CD strategy, frontend guide
docs/api/              openapi-baseline.json (committed contract snapshot)
```

---

## 5. Core Invariants

Five properties the system must never violate. Every one has a test.

1. **Conservation of value.** Wallet balance always equals the sum of its settled
   ledger entries. Verified end-to-end in
   `test_sprint4_pilot_business_day.py`.
2. **No double-spend.** Withdrawals take `SELECT FOR UPDATE` on the wallet row.
   Verified against 50 parallel attacks.
3. **Idempotency.** A UNIQUE index on `transactions.idempotency_key` makes
   retries inert. This index is load-bearing —
   `test_sprint4_migration_model_parity.py` asserts it is genuinely *enforced*,
   not merely present.
4. **No stranded holds.** A telco failure — graceful or an exception — must
   release `locked_balance`. Verified under sustained outage and concurrency.
5. **Schema parity.** `alembic upgrade head` must produce exactly the model
   schema. Gated in CI and again before production release.

---

## 6. Running the System

### Local (SQLite)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn payday.main:app --host 0.0.0.0 --port 8000
```

- Landing/status page — `http://localhost:8000/`
- Swagger UI — `/docs` · ReDoc — `/redoc` · Spec — `/openapi.json`
- Health — `/api/v1/public/health`

### Docker Compose (PostgreSQL)

```bash
docker compose up --build
```

Brings up `db` (Postgres 15, health-gated), `migrate` (one-shot
`alembic upgrade head`, gated on db health), and `api` (gated on migrations
completing). No Redis service is defined — see §9.2.

### Tests

```bash
pytest -q                                  # all 101
pytest tests/test_sprint4_load_capacity.py -s   # with latency output
```

---

## 7. Configuration

All settings come from environment variables or `.env`
(`src/payday/core/config.py`).

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite+aiosqlite:///./payday.db` | Use `postgresql+asyncpg://…` in production |
| `SECRET_KEY` | dev placeholder | **Must be replaced.** JWT signing |
| `ENCRYPTION_KEY` | dev placeholder | **Must be replaced.** AES-256-GCM for PII |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | |
| `BACKEND_CORS_ORIGINS` | localhost dev origins | Explicit allowlist — see below |
| `BACKEND_CORS_ORIGIN_REGEX` | anchored `*.e2b.app` | Ephemeral preview hosts |
| `DEFAULT_DAILY_LIMIT` | `500000.00` XAF | |
| `DEFAULT_DEPOSIT_FEE_PERCENTAGE` | `0.005` | 0.5% |
| `DEFAULT_WITHDRAW_FEE_PERCENTAGE` | `0.01` | 1.0% |
| `MIN_FEE_AMOUNT` | `25.00` XAF | |

> **Never set `BACKEND_CORS_ORIGINS=["*"]`.** Combined with
> `allow_credentials=True`, Starlette reflects the caller's Origin and marks the
> response credentialed — any website could then call this API cross-origin and
> read the replies. This was a live defect, fixed in Sprint 4; a regression test
> guards it.

**Rotating `ENCRYPTION_KEY`** requires decrypting and re-encrypting every stored
PII field. There is no automated rotation path today. Treat the key as
long-lived and store it in a managed secret store.

---

## 8. Operations Runbook

### Health & liveness

`GET /api/v1/public/health` → `{"data": {"status": "UP", ...}}`. Used by the
container `HEALTHCHECK`, both CD pipelines, and orchestrator probes. It performs
no database I/O (p95 ≈ 0.43 ms), so it is safe to poll frequently.

### Deployment

See `docs/CI_CD_PIPELINE_STRATEGY.md`. Summary: merge to `main` → staging; tag
`v*.*.*` → production, gated on reviewer approval, with a verified backup before
migrations and automatic rollback on failed health checks.

### Common incidents

**Deposits/withdrawals returning 500 after a fresh deploy.**
Check schema parity first:
```bash
alembic current && alembic heads
alembic revision --autogenerate -m "check"   # must contain no op.* calls
```
A non-empty revision means the database has drifted from the models. This is the
exact failure mode of the pre-`002` schema.

**Customer reports funds "stuck".**
Inspect `locked_balance` on the wallet. Non-zero with no in-flight transaction
means a hold outlived its transaction. Cross-reference `audit_logs` for
`FUNDS_HELD` without a matching release, then reconcile the channel for the
period.

**Reconciliation reports variance.**
`POST /api/v1/admin/reconcile` with the partner statement.
`MISSING_IN_PARTNER` means PayDay settled something the telco did not report —
do **not** auto-reverse; confirm with the provider first.

**Wallet auto-frozen.**
Five consecutive bad PINs freeze the wallet and dispatch a security alert.
Unfreeze via `POST /api/v1/admin/wallets/{wallet_id}/status` after identity
verification. Note the counter is per-process — see §9.2.

**Suspected webhook replay.**
Webhooks are HMAC-SHA256 verified with anti-replay guards. Check `audit_logs` for
repeated `external_ref` values; balances should show a single credit.

### Audit trail

Every state change writes to `audit_logs` (actor, action, entity, before/after,
IP). Read-only via `GET /api/v1/admin/audit-logs`. Never mutate this table.

---

## 9. Known Gaps

**Read this section before any production decision.**

### 9.1 No production load test — *largest gap*
Sprint 4 measured in-process ASGI over SQLite: balance p95 **3.60 ms**, 399 req/s
for 200 concurrent reads. These exclude network and PostgreSQL and **do not
establish a production p95**. A load test against PostgreSQL 15 with realistic
pooling is required before pilot.

### 9.2 Redis and Celery are not wired — *blocks horizontal scaling*
`services/task_queue.py` is an in-process simulation. The PIN-attempt counter in
`transaction_manager.py` is an in-memory dict.

> **The API cannot currently run more than one replica.** The 5-attempt PIN
> lockout is per-process, so N replicas give an attacker 5N attempts before any
> freeze. Queued work also does not survive a restart.

Either move the counter to Redis, or formally accept single-replica operation and
record it as a capacity ceiling. `docker-compose.yml` defines no Redis service
rather than ship a broker nothing connects to.

### 9.3 UBA Bank adapter — Phase 2
The factory returns a clear `CHANNEL_NOT_AVAILABLE`. The port interface
(`adapters/base.py`) is ready for it.

### 9.4 Container image unbuilt in this environment
No Docker daemon was available during Sprint 4. The `Dockerfile` was validated by
executing its stages directly: dependency resolution, package completeness (all
32 paths importable from the installed wheel alone), and `alembic upgrade head`.
The `docker` job in `ci.yml` builds it, smoke-tests health, and asserts non-root
— **that job has not yet run on GitHub.**

### 9.5 Deployment targets unconfigured
Both CD pipelines are complete but their GitHub Environments have no secrets.
They fail in `preflight` rather than reporting a green deploy that deployed
nothing. See `docs/CI_CD_PIPELINE_STRATEGY.md` §7.

### 9.6 Development secrets in `.env.example`
`SECRET_KEY` and `ENCRYPTION_KEY` are placeholders. Replace before any
deployment.

### 9.7 Six designed features have no backend
`docs/FRONTEND_INTEGRATION_GUIDE.md` §4 maps all 19 Figma screens against the
live contract. Most map cleanly; six designed features cannot be built because
no endpoint exists:

| Gap | Screen | Missing capability |
| --- | --- | --- |
| §4.1 | Login | PIN-based login, **and any password/PIN reset at all** |
| §4.2 | Dashboard, Transaction History | P2P "Send", bill payments, bank channel (UBA is Phase 2) |
| §4.3 | Notifications Feed | Notification categories and **read/unread state** |
| §4.4 | Registration Page | `referral_code` field (currently silently discarded) |
| §4.5 | Verify Account 1 & 2 | **File upload for ID documents and selfie** — the largest gap |
| §4.7 | Receipt screens | Recipient-name resolution for a destination MSISDN |

Two are launch-blocking irrespective of the design:

- **No password reset exists anywhere in the API.** A user who forgets their
  password is permanently locked out. (A `ChangePasswordRequest` schema is
  defined in `schemas/auth.py` but no route uses it.)
- **KYC reviewers have no documents to review.** `POST /kyc/submit` accepts only
  a document *number*; the admin queue can show a masked string and nothing
  else, so identity verification cannot actually be performed.

A further seven items need a design adjustment rather than backend work — fee
direction on deposits, FCFA/XAF label inconsistency, the password prompt on PIN
change, client-side PDF generation, and unsupported profile items. All are
listed in §4 of the guide with recommended resolutions.

---

## 10. Pilot Readiness

**AMBER.** The ledger holds under concurrency and fault injection, value is
conserved end-to-end, reconciliation detects variance, and the schema matches the
models.

Two conditions before taking real customer money:

1. Load test against PostgreSQL 15 with production-like pooling (§9.1).
2. Wire Redis for the PIN counter, **or** formally accept single-replica
   operation (§9.2).

---

## 11. Handover Checklist

- [x] Ledger engine, double-entry, row locking
- [x] MTN MoMo + Orange Money adapters, HMAC webhooks, anti-replay
- [x] RBAC back-office, reconciliation engine
- [x] 101 automated tests, all passing
- [x] Migration/model parity gate
- [x] Containerization and four CI/CD pipelines
- [x] OpenAPI 3.1 contract + committed baseline
- [x] Frontend integration guide — all 19 screens mapped (§9.7)
- [ ] Production load test (§9.1)
- [ ] Redis-backed PIN counter or accepted single-replica ceiling (§9.2)
- [ ] Deployment environment secrets (§9.5)
- [ ] Production secrets rotated (§9.6)
- [ ] Password-reset endpoint — launch-blocking (§9.7)
- [ ] KYC document upload — launch-blocking (§9.7)
- [ ] UBA Bank adapter (Phase 2)

---

## 12. Reference

| Document | Contents |
| --- | --- |
| `docs/reports/SPRINT_1_REPORT.md` | Ledger, concurrency, PII encryption |
| `docs/reports/SPRINT_2_REPORT.md` | MTN adapter, state machine, webhooks |
| `docs/reports/SPRINT_3_REPORT.md` | Orange adapter, RBAC, reconciliation |
| `docs/reports/SPRINT_4_REPORT.md` | Hardening, chaos, security, pilot |
| `docs/CI_CD_PIPELINE_STRATEGY.md` | Pipelines, migrations, rollback |
| `docs/FRONTEND_INTEGRATION_GUIDE.md` | Screen-by-screen client integration |
| `docs/api/openapi-baseline.json` | Committed contract snapshot |
