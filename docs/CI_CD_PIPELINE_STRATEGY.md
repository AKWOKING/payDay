# PayDay — CI/CD Pipeline Strategy

**Version:** 1.1 · **Date:** 2026-09-07
**Applies to:** `AKWOKING/payDay` backend (FastAPI / SQLAlchemy 2.0 / PostgreSQL 15)

> **Status.** The four workflow files under `.github/workflows/` are present in
> the repository but have **never been pushed or run**: the GitHub connection
> lacks the `workflows` scope (roadmap risk R8), and pushes containing
> `.github/workflows/` are rejected wholesale. They were reconstructed from
> this document on 2026-09-07 after the previous session's copy was lost in a
> sandbox re-clone. The strategy below remains the specification; nothing in
> it is verified by a green GitHub Actions run yet.

---

## 1. Overview

Four workflows in `.github/workflows/`:

| Workflow | Trigger | Purpose |
| --- | --- | --- |
| `ci.yml` | every push and PR | Tests, migrations, contract, image build |
| `cd-pilot-staging.yml` | push to `main`, manual | Release to pilot staging |
| `cd-production.yml` | `v*.*.*` tag, manual | Gated production release |
| `generate-client-sdks.yml` | API changes, releases | Dart + TypeScript clients |

Two principles run through all of them.

**Fail loudly, never silently.** Each CD pipeline opens with a `preflight` job
that asserts its environment secrets exist. A deploy pipeline reporting success
while deploying nothing is worse than one that fails — it destroys trust in the
green checkmark.

**Deploy the artefact you tested.** Rollouts reference an immutable image
**digest**, not a floating tag. `staging-latest` can move between the build and
the rollout; `@sha256:…` cannot.

---

## 2. CI (`ci.yml`)

Four parallel jobs:

### `test`
Installs with `pip install -e ".[dev]"` and runs the full suite (121 tests as of
2026-09-07), uploading a JUnit report. A `redis:7` service container is started
and `PAYDAY_TEST_REDIS_URL=redis://localhost:6379/0` is exported so the
real-Redis integration tests (`tests/test_sprint5_shared_infrastructure.py`)
run against actual Redis instead of skipping.

### `migrations-postgres`
The suite runs on SQLite; production is PostgreSQL. This job closes that gap
against a real `postgres:15-alpine` service:

1. `alembic upgrade head` on a clean database.
2. `alembic upgrade head` again — must be a no-op (idempotence).
3. **Drift gate:** `alembic revision --autogenerate` against the up-to-date
   database. A correct migration chain produces an *empty* revision. If the
   generated file contains any `op.` call, models and migrations have diverged
   and the build fails with the offending operations printed.

Step 3 is the check that would have caught the `transactions.linked_account_id`
`NOT NULL` defect described in `docs/reports/SPRINT_4_REPORT.md` §3.1 — a defect
that 58 passing tests missed because they built their schema from the models and
never touched a migration.

### `contract`
Exports `/openapi.json`, asserts OpenAPI 3.1 and a non-empty path set, and
uploads the document for the SDK workflow.

### `docker`
Builds the runtime image with Buildx and GHA layer caching, then:
- runs the container and polls `/api/v1/public/health` until healthy (60s budget);
- asserts the container **does not run as root** (`docker run --entrypoint id -u` must not be `0`).

---

## 3. Staging (`cd-pilot-staging.yml`)

```
preflight ─┐
           ├─► build-and-push ─► migrate ─► deploy ─► smoke
test ──────┘
```

- **preflight** — asserts `STAGING_DATABASE_URL`, `STAGING_DEPLOY_HOOK`, `STAGING_BASE_URL`.
- **test** — the suite runs again on this exact commit. Never promote a build whose tests were not run on the promoted SHA.
- **build-and-push** — publishes to GHCR as `staging-latest` and `staging-<sha>`.
- **migrate** — prints `alembic current` and `alembic heads`, then upgrades.
- **deploy** — POSTs the immutable digest to the platform hook, then polls health for up to 10 minutes.
- **smoke** — health, OpenAPI 3.1, and the fee calculator (the landing page depends on it), then diffs the *deployed* contract against `docs/api/openapi-baseline.json`.

---

## 4. Production (`cd-production.yml`)

Triggered only by a `v*.*.*` tag, or a manual dispatch that requires typing the
version to confirm. The `production` GitHub Environment should have **Required
reviewers** enabled, which pauses every job bound to it for human approval.

```
preflight ─► test ─► build-and-push ─► backup ─► migrate ─► deploy ─► post-release
                                                              │
                                                              └─► rollback (on failure)
```

Differences from staging:

- **Version validation** — the tag must match `^v[0-9]+\.[0-9]+\.[0-9]+$`.
- **Postgres in `test`** — the suite runs *and* migrations are applied to a real PostgreSQL 15 service.
- **`backup`** — requests a snapshot and **fails closed** unless the response confirms completion. A migration without a verified restore point is not reversible.
- **Drift gate after migrating** — the same autogenerate check as CI, run against production. Production must never serve on a drifted schema.
- **Automatic rollback** — if health checks fail, the previous digest is redeployed.

---

## 5. Database Migrations: expand/contract

Migrations run **before** the new image serves traffic, and during a rolling
deploy both versions run against the same schema. Every migration must therefore
be backward-compatible with the currently-deployed code.

Use three releases for any breaking change:

| Release | Migration | Application |
| --- | --- | --- |
| **N (expand)** | Add the new nullable column / index | Writes both old and new |
| **N+1 (migrate)** | Backfill | Reads new, still writes both |
| **N+2 (contract)** | Drop the old column, add `NOT NULL` | Uses new only |

Never in a single release: renaming a column, adding `NOT NULL` without a
default, or dropping anything still referenced.

**Rollback asymmetry.** Application rollback is automatic; the database is not
rolled back. `downgrade()` on a migration that dropped a column cannot restore
its data. This is why `backup` fails closed. Recovery from a bad migration is a
restore from the snapshot, not an `alembic downgrade`.

Add indexes on large tables with `CREATE INDEX CONCURRENTLY` (outside a
transaction) to avoid holding a write lock. Migration `002` creates 18 indexes;
on a populated production table it should be applied during a maintenance window
or rewritten to use the concurrent form.

---

## 6. Client SDKs (`generate-client-sdks.yml`)

Runs when `src/payday/api/**`, `src/payday/schemas/**`, or `main.py` change.

1. **export-spec** — exports the document and asserts OpenAPI 3.1, a declared
   security scheme, and a unique `operationId` on every operation (generators
   emit colliding methods otherwise). Then **fails if
   `docs/api/openapi-baseline.json` is stale**, printing added and removed
   operations. Regenerate the baseline in the same commit as the API change.
2. **dart-client** — `dart-dio` generator for the Flutter app.
3. **typescript-client** — `typescript-angular` generator for both Angular apps.
4. **attach-to-release** — on a published release, uploads both SDKs and the spec.

SDKs are **build artefacts, not committed code**. Checked-in generated clients
rot the moment someone edits them by hand.

---

## 7. Required Configuration

Create two GitHub Environments. Enable **Required reviewers** on `production`.

**`pilot-staging`**

| Secret | Example |
| --- | --- |
| `STAGING_DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/payday_staging` |
| `STAGING_DEPLOY_HOOK` | `https://platform.example/hooks/deploy/staging` |
| `STAGING_BASE_URL` | `https://staging-api.payday.cm` |

**`production`**

| Secret | Example |
| --- | --- |
| `PRODUCTION_DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/payday` |
| `PRODUCTION_DEPLOY_HOOK` | `https://platform.example/hooks/deploy/production` |
| `PRODUCTION_BASE_URL` | `https://api.payday.cm` |
| `PRODUCTION_BACKUP_HOOK` | `https://platform.example/hooks/backup` |

Image publishing uses the built-in `GITHUB_TOKEN` with `packages: write`; no
registry secret is needed for GHCR.

The deploy hooks are expected to accept
`POST {"image": "<registry>/<repo>@sha256:..."}`. Adapt the `deploy` step to your
platform's API — that step is the only platform-specific code in the pipelines.

---

## 8. Branching & Release

- Work on a feature branch; open a PR. `ci.yml` runs on every push.
- Merge to `main` → `cd-pilot-staging.yml` deploys to staging.
- Soak on staging, then tag: `git tag v1.1.0 && git push origin v1.1.0`.
- `cd-production.yml` runs and waits for reviewer approval.

Never tag a commit that has not been through staging.

---

## 9. Local Equivalents

Run these before pushing to avoid a red build:

```bash
pytest -q                                   # full suite
alembic upgrade head && alembic upgrade head  # idempotence
docker compose up --build                   # full stack
docker compose run --rm migrate             # migrations only
```

Reproduce the CI drift gate locally:

```bash
alembic revision --autogenerate -m "drift-check"
# open the generated file — it must contain no op.* calls, then delete it
```
