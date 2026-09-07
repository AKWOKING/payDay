# Sprint 4 Report — Hardening, Chaos, Security Review & Pilot Readiness

**Project:** PayDay — Integrated e-Wallet for Cameroon (MTN MoMo, Orange Money, UBA)
**Sprint:** 4 (Weeks 7–8)
**Status:** **COMPLETED** — 101 automated tests (100 passing, 1 environment-skipped)
**Date:** 2026-09-07

---

## 1. Scope

Sprint 3 closed with a GREEN readiness verdict and this scope for Sprint 4:
concurrency hardening, chaos testing, security review, and pilot readiness. This
sprint executed that scope and added the containerization and CI/CD work needed
to deploy the result.

An explicit note on provenance, because it matters for how these results should
be read: at the start of this sprint the repository contained Sprints 1–3 only
(58 tests). There was no `Dockerfile`, no `docker-compose.yml`, no
`.github/workflows/`, and no Sprint 4 artefacts of any kind. Everything below
was built and measured during this sprint. Nothing in this report is carried
over from a prior claim.

---

## 2. Test Suite

| Sprint | Tests | Status |
| --- | ---: | --- |
| Sprint 1 — ledger, concurrency, PII | 6 | Passing |
| Sprint 2 — MTN, webhooks, state machine | 14 | Passing |
| Sprint 3 — Orange, RBAC, reconciliation | 20 | Passing |
| Sprint 4 — hardening, chaos, security, pilot | 43 | 42 passing, 1 skipped |
| Baseline suites (auth, wallet, admin, KYC, public) | 18 | Passing |
| **Total** | **101** | **100 passing, 1 skipped** |

The single skip is `test_alg_none_token_is_rejected`. The installed `python-jose`
build refuses to *mint* an `alg: none` token, so the attack cannot be constructed
locally; the test skips rather than reporting a false pass. The rejection path
itself is covered by the forged-signature and malformed-header tests.

Run:

```bash
pip install -e ".[dev]"
pytest -q
```

---

## 3. Defects Found and Fixed

Sprint 4 found three genuine defects. Two were reachable in production.

### 3.1 Migration/model schema drift — *production-breaking*

`alembic/versions/001_initial_schema.py` had diverged from the SQLAlchemy models
in three ways:

| Item | Migration | Model | Consequence |
| --- | --- | --- | --- |
| `transactions.linked_account_id` | `NOT NULL` | `nullable=True` | **INSERT fails on PostgreSQL** |
| Same column's FK | `ondelete=RESTRICT` | `ondelete=SET NULL` | Account unlinking blocked by history |
| Indexes | 4 created | 22 declared | 18 missing → sequential scans |

The nullability mismatch was the serious one.
`services/transaction_manager.py` (lines 138 and 304) computes:

```python
linked_account_id = linked_account.linked_account_id if linked_account else None
```

and passes that straight into `Transaction(...)`. Any deposit or withdrawal
without a linked external account inserts `NULL` into a `NOT NULL` column, so on
a migrated PostgreSQL database the request raises `IntegrityError` and returns
500.

**Why 58 passing tests never caught it.** `tests/conftest.py` builds its schema
with `Base.metadata.create_all()` — from the *models*. Production is provisioned
with `alembic upgrade head` — from the *migrations*. Nothing compared the two, so
the entire suite ran against a schema that production never uses.

The missing indexes were the quieter half. They included
`transactions.wallet_id`, `.status`, `.type` and `.external_ref` — exactly the
predicates behind transaction history, the settlement reconciliation sweep, and
webhook external-reference lookups. Every one of those would have become a
sequential scan under real volume.

**Fix.** `alembic/versions/002_fix_schema_drift.py` corrects nullability, the FK
delete rule, and all 18 missing indexes. It is idempotent, reversible
(`downgrade` verified), and dialect-aware — PostgreSQL takes a direct `ALTER`
path, SQLite a `batch_alter_table` rebuild with an explicit `copy_from`.

One trap worth recording: the SQLite batch rebuild initially *dropped* the
`UNIQUE` index on `idempotency_key`, because 001 created it outside the table
definition. That index is the anti-double-spend guard. It is now declared inside
the `copy_from` table, and
`test_idempotency_key_uniqueness_survives_migration` asserts the constraint is
genuinely *enforced* — by attempting a duplicate insert — not merely present by
name.

**Regression guard.** `tests/test_sprint4_migration_model_parity.py` runs the
real migration chain and diffs the result against `Base.metadata` using Alembic's
`compare_metadata`. All four tests were verified to **fail** against the
pre-fix schema and pass after it. The same gate runs in CI against PostgreSQL 15
and again in `cd-production.yml` before release.

### 3.2 Credentialed wildcard CORS — *security*

`main.py` configured:

```python
allow_origins=["*"],
allow_credentials=True,
```

Starlette resolves that combination by reflecting the caller's `Origin` and
setting `Access-Control-Allow-Credentials: true`. Verified live against the
running server:

```
$ curl -i localhost:8000/api/v1/public/health -H "Origin: https://evil-attacker.example"
access-control-allow-origin: https://evil-attacker.example
access-control-allow-credentials: true
```

Any website could therefore issue credentialed cross-origin requests to this
financial API and read the responses. `settings.BACKEND_CORS_ORIGINS` already
existed as an allowlist — it was simply never referenced.

**Fix.** The middleware now uses the configured allowlist plus an anchored regex
for ephemeral preview hosts (`^https://[a-z0-9-]+\.e2b\.app$` — anchored so
`https://e2b.app.evil.com` does not match), and narrows `allow_methods` and
`allow_headers` from `*` to the set actually used. Verified after the fix:
the hostile origin receives no `Access-Control-Allow-Origin` header, while
`http://localhost:4200` and the preview host still do.

`test_cors_does_not_reflect_arbitrary_origins` was confirmed to fail against the
old configuration.

### 3.3 `ProblemDetail` absent from the OpenAPI contract — *contract*

Every endpoint returns RFC 7807 errors, but the `ProblemDetail` schema was never
published in `components/schemas`, because no operation declared it as a response
model. Generated Flutter and Angular SDKs therefore had no typed error model and
each client would have hand-rolled parsing against an undocumented shape.

**Fix.** Declared as the default 400/401/403/404/409/422/500 response on the v1
router, so it is published once and documented on every operation.

### 3.4 Undeclared dependency (found during environment setup)

`pyproject.toml` used pydantic's `EmailStr` without declaring `email-validator`
or the `pydantic[email]` extra. A clean install failed at import, making the
suite uncollectable. Fixed in commit `9a0bc90`.

---

## 4. Load & Capacity

**Measured on:** 2 vCPU sandbox, Python 3.11.2, in-process ASGI over SQLite.

| Endpoint | p50 | p95 | p99 | max |
| --- | ---: | ---: | ---: | ---: |
| `GET /api/v1/wallet/balance` | 3.23 ms | **3.60 ms** | 3.84 ms | 3.97 ms |
| `POST /api/v1/public/fee-calculator` | 0.47 ms | **0.67 ms** | 0.77 ms | 0.86 ms |
| `GET /api/v1/public/health` | 0.37 ms | **0.43 ms** | 0.66 ms | 0.74 ms |
| `GET /api/v1/wallet/transactions` (40 rows) | 5.53 ms | 5.87 ms | 5.87 ms | 6.20 ms |

Throughput: **399 req/s** for 200 concurrent balance reads, zero errors.
History scaling: **1.12x** p50 growth from an empty ledger to 40 rows at a
constant 20-row page — no superlinear degradation, so no N+1 or missing index on
that path.

### What these numbers are not

They exclude network latency and PostgreSQL. They are in-process ASGI over
SQLite, which means they characterise *application* cost — routing, validation,
ORM, serialisation — and nothing else. **They do not establish a production
p95.** Treat them as a regression guard against algorithmic blow-ups.

A methodological note, since it changed the result materially. The first draft of
this suite timed each request inside a 200-way `asyncio.gather` and reported a
p95 of **641 ms**. That figure was almost entirely queueing delay: the test
harness shares a single SQLite connection via `StaticPool`, so concurrent
requests serialise and each stopwatch included time spent waiting behind the
other 199. Latency percentiles are now taken only from serial measurement, and
concurrency is reported separately as aggregate throughput. The 641 ms figure was
an artefact of the measurement, not a property of the system.

**Before pilot, a load test against real infrastructure is still required** —
PostgreSQL 15, connection pooling, and network hops. See Section 8.

---

## 5. Chaos & Fault Injection

Sprints 2–3 covered *graceful* telco failure (`success=False` → compensating
release). Sprint 4 covers the harder case: the adapter **raises** — connection
reset, DNS failure, read timeout, partition mid-disbursement.

| Scenario | Injected | Result |
| --- | --- | --- |
| Withdrawal, connect timeout | `httpx.ConnectTimeout` | Failure returned; `balance` and `locked_balance` unchanged |
| Deposit, read timeout | `httpx.ReadTimeout` | No credit applied |
| Graceful payout rejection | `success=False` | Hold released, balance intact |
| Sustained outage, 5 consecutive failures | `httpx.ConnectError` ×5 | No accumulation in `locked_balance` |
| 10 concurrent withdrawals into a partition | `ConnectError` + interleaving | Value conserved, no stranded holds |
| Recovery after partition heals | outage then restore | Deposits resume and credit correctly |

The controlling risk was stranded holds. `hold_funds` increments
`locked_balance` in the session *before* the telco call, so an exception mid-call
must unwind. It does: the hold is uncommitted, and `get_db` rolls back. Confirmed
under concurrency, which is where compensating logic usually breaks.

**Harness note.** These use a client with `raise_app_exceptions=False`. httpx's
default re-raises unhandled server exceptions straight into the test, which is
not what a real caller sees — under uvicorn, Starlette converts the same
exception into an RFC 7807 500. The tests assert against production behaviour.

---

## 6. Security Review (OWASP API Top 10)

| Category | Test | Result |
| --- | --- | --- |
| API2 Broken Authentication | Token re-signed with attacker key | 401 |
| API2 | `alg: none` downgrade | Skipped — library refuses to mint |
| API2 | Expired token | 401 |
| API2 | 6 malformed `Authorization` headers | 401/403, never 500 |
| API1 Broken Object Level Authz | Read another user's transaction | 403/404, no amount leaked |
| API3 Mass assignment | `role: ADMIN` at registration | Ignored |
| API3 | `kyc_status: VERIFIED` at registration | Ignored |
| Injection | 4 SQL payloads on a path parameter | No 500, no credential leak, table intact |
| Sensitive data | `password_hash` / `pin_hash` / bcrypt in responses | Absent |
| Sensitive data | National ID after KYC submit | Masked, plaintext absent |
| API8 Misconfiguration | Credentialed wildcard CORS | **Found and fixed** (§3.2) |
| API8 | Stack traces in error bodies | Absent |
| Access control | 10 protected routes, unauthenticated | All 401/403 |

The unauthenticated sweep is a standing guard: it enumerates the protected
surface so a route added later without an auth dependency fails the build.

---

## 7. Contract & Pilot Verification

**Contract drift** (`test_sprint4_contract_drift.py`, 10 tests). The committed
snapshot `docs/api/openapi-baseline.json` (OpenAPI 3.1.0, 32 paths) is diffed
against the live document. Removing a path or method fails the build, as does
adding a newly-required field to an existing request schema — both break
already-shipped mobile clients that cannot be rolled back. Also asserted: bearer
scheme declared, every protected operation declares security, `ProblemDetail`
published, operationIds unique, and the served `/openapi.json` matches the
in-process document.

**Pilot business day** (`test_sprint4_pilot_business_day.py`, 5 tests). Three
customers transacting across both channels, plus an operations reversal and the
settlement sweep. The controlling assertion is **conservation of value**: each
wallet's closing balance is reconstructed from its own ledger and compared to the
reported balance. Also verified: no funds remain locked at end of day; a reversal
restores the pre-transaction balance exactly; a second reversal of the same
transaction is refused without double-debiting; reconciliation matches a
self-consistent partner statement with zero variance; and reconciliation *detects*
a deliberately incomplete statement — a sweep that never reports variance is
useless.

---

## 8. Known Gaps — Not Closed in Sprint 4

Stated plainly, because the pilot decision depends on them.

1. **No production load test.** Section 4 measures application cost over SQLite
   in-process. A test against PostgreSQL 15 with realistic pooling and network
   latency has not been run. **This is the largest gap.**

2. **Redis and Celery are not wired.** The architecture specifies both.
   `services/task_queue.py` is an in-process simulation and the PIN-attempt
   counter in `transaction_manager.py` is an in-memory dict. Consequences:
   - the API **cannot be scaled beyond one replica** — the 5-attempt PIN lockout
     is per-process, so N replicas give an attacker 5N attempts;
   - queued work does not survive a restart.
   `docker-compose.yml` deliberately defines no Redis service rather than ship a
   broker nothing connects to.

3. **UBA Bank adapter** remains Phase 2; the factory returns a clear
   `CHANNEL_NOT_AVAILABLE`.

4. **Deployment targets are unconfigured.** The CD pipelines are complete but
   their environments have no secrets. They fail loudly in a `preflight` job
   rather than reporting a green deploy that deployed nothing.

5. **Container build is unverified in this environment.** No Docker daemon was
   available in the sandbox. The `Dockerfile` logic was validated by executing
   its stages directly — dependency resolution, package completeness (32 paths
   importable from the installed wheel alone), and `alembic upgrade head`. The
   image build itself is exercised by the `docker` job in `ci.yml`, which also
   smoke-tests the health endpoint and asserts the container does not run as
   root. **That job has not yet run on GitHub.**

6. **Secrets are development defaults.** `SECRET_KEY` and `ENCRYPTION_KEY` in
   `.env.example` are placeholders and must be replaced before any deployment.

---

## 9. Pilot Readiness Verdict

**AMBER — ready for a controlled pilot once gaps 1 and 2 are addressed.**

The ledger holds under concurrency and fault injection, value is conserved
end-to-end, the reconciliation engine detects variance, and the schema now
matches the models. The migration defect in §3.1 would have caused production
500s on the first deposit without a linked account, and it was invisible to a
fully green test suite — which is the strongest argument for the parity gate now
standing in CI.

Two conditions before taking real customer money:

1. Run a load test against PostgreSQL 15 with production-like pooling.
2. Wire Redis for the PIN-attempt counter, **or** formally accept single-replica
   operation and document it as a capacity ceiling. Scaling out today silently
   weakens the brute-force lockout.

---

## 10. Artefacts

| Path | Purpose |
| --- | --- |
| `Dockerfile` | Multi-stage, non-root, healthcheck |
| `docker-compose.yml` | Postgres + gated Alembic migrate + API |
| `.github/workflows/ci.yml` | Tests, Postgres migrations + drift gate, contract export, image build & smoke |
| `.github/workflows/cd-pilot-staging.yml` | Staging release with post-deploy contract check |
| `.github/workflows/cd-production.yml` | Tag-gated release, backup, drift gate, auto-rollback |
| `.github/workflows/generate-client-sdks.yml` | Dart/Flutter + TypeScript/Angular SDKs |
| `alembic/versions/002_fix_schema_drift.py` | Schema drift correction |
| `docs/api/openapi-baseline.json` | Committed contract snapshot |
| `tests/test_sprint4_*.py` | 43 Sprint 4 tests |
