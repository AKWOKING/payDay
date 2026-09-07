# PayDay — Review Guide

**Version:** 1.1 · **Date:** 2026-09-07
**Branch:** `arena/01a07cf0-payday` · **Applies to:** `AKWOKING/payDay`

A reviewer only gets a few minutes. This guide gives three reading paths — one
for product, one for frontend, one for DevOps — plus an explicit "what has
actually been verified" list. It is written so nobody has to trust a summary
claim: every path points at the file, section, or test that proves it.

> **Provenance note.** A first version of this guide committed in a previous
> session (`f05980a`) was lost when the sandbox re-cloned the repository: it
> was never pushed, and neither was the CI/CD workflow commit (`8fa7ff1`).
> Neither exists on `origin`, in PR #4's commit list, or as a dangling object.
> This version was re-authored against the current verified state of the repo,
> and the workflows were reconstructed from
> `docs/CI_CD_PIPELINE_STRATEGY.md` (the committed design document for them).

---

## 0. Getting to a running system

```bash
cd /home/user/payDay
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q -p no:logging     # 121 passed, 2 skipped (2026-09-07)
.venv/bin/python -m uvicorn payday.main:app --host 0.0.0.0 --port 8000
# Interactive docs    → http://127.0.0.1:8000/docs
# OpenAPI 3.1         → http://127.0.0.1:8000/openapi.json
# Health (incl. Redis) → http://127.0.0.1:8000/api/v1/public/health
```

Local development uses `COUNTER_BACKEND=memory` (no Redis needed). Production
must use `COUNTER_BACKEND=redis` — the app refuses to start otherwise
(fail-closed, see §DevOps below).

---

## 1. Path A — Product reviewer

Start with the question: *"can this hold real customer money?"* The honest
answer today is **still AMBER — better than last week, not green yet.**

1. **`docs/LAUNCH_BLOCKER_ROADMAP.md`** — the source of truth for what is
   missing. §1 lists the seven blockers (LB-1…LB-7). §2 lists the **22 product
   decisions** — every one is a question someone outside engineering must
   answer; the gating ones are **D1** (SMS aggregator contract, procurement
   lead time), **D6/D7** (KYC storage jurisdiction + manual-vs-vendor review),
   **D21** (is Redis approved as production infrastructure?) and **D18**
   (expected pilot volume — without it a load test proves nothing). §5 is the
   sequencing, §6 the risks, §7 the definition of "launch-ready". §0 is new:
   it records that WS-0/WS-3/WS-5 were implemented on 2026-09-07.
2. **`docs/reports/SPRINT_4_REPORT.md`** — the AMBER verdict and the measured
   load figures. **Read the caveat on them**: they are in-process ASGI over
   SQLite (balance p95 ≈ 3.6 ms) and explicitly **not** a production capacity
   claim. LB-5 replaces them.
3. **`docs/reports/SPRINT_5_REDIS_AND_THROTTLING.md`** — what changed this week:
   login throttling, a cross-replica PIN lockout, and the fail-closed rule.
   §5 lists what is still deliberately not done.
4. **`docs/FINAL_PROJECT_HANDOVER.md`** — architecture, invariants, runbook,
   and §9 (known gaps). Section 9.2 was updated to say Redis is wired for the
   shared counters and Celery is still not.

**The three questions to escalate:** D1, D6/D7, D21 — in that order; D1 has the
longest procurement lead time and gates the password-reset critical path.

---

## 2. Path B — Frontend reviewer (Flutter mobile, Angular landing/admin)

The contract you build against is the OpenAPI document and the screen map —
not the code.

1. **`docs/FRONTEND_INTEGRATION_GUIDE.md`** — v2.0 maps all **19 Figma frames**
   to live API calls and registers the design gaps in §4 (P2P send, bill
   payments, notification categories, referral codes, PIN login, recipient
   names, avatars/QR/tiers). If your screen is in §4, that feature does **not**
   exist yet — do not build against it.
2. **`docs/api/openapi-baseline.json`** — the committed contract: OpenAPI 3.1,
   **32 paths**, bearable JWT security scheme, RFC 7807 `ProblemDetail` in
   components. Regenerate it (never hand-edit) after any API change:

   ```bash
   python -c "import json,sys; sys.path.insert(0,'src'); from payday.main import app; \
   json.dump(app.openapi(), open('docs/api/openapi-baseline.json','w'), indent=2, \
   ensure_ascii=False, sort_keys=True)"
   ```

   The contract test (`tests/test_sprint4_contract_drift.py`) fails the build
   on **removed** operations or **newly required** request fields — additions
   are fine. 429 and 503 are now declared on all operations (Sprint 5):
   **handle `429 RATE_LIMITED` with the `Retry-After` header** in the mobile
   client's login/refresh paths, and `503 REDIS_UNAVAILABLE` as retry-later.
3. **Auth flows to know:** `POST /auth/register` (201), `POST /auth/login`
   (200; unchanged shape), `POST /auth/refresh`, `POST /auth/set-pin`
   (4–6 digit PIN, requires current password), `GET /auth/me`. Login is now
   throttled (5 per phone / 15 min) — the UI should surface a clean message
   for `429`.
4. **Public pages:** `GET /public/health` (now includes `redis.status`),
   `GET /public/info`, `POST /public/fee-calculator` (deposit 0.5 %,
   withdrawal 1.0 %; `total_charged`/`net_credited` differ by direction).
5. **SDK generation** is documented in `docs/CI_CD_PIPELINE_STRATEGY.md` §6
   (Dart + TypeScript Angular, attached to releases; generated files are
   artefacts, never committed). **That workflow has never run on GitHub** —
   the export/spec step is reproducible locally from `/openapi.json`.

---

## 3. Path C — DevOps reviewer

1. **`docs/CI_CD_PIPELINE_STRATEGY.md`** — the design for four workflows
   (`ci.yml`, `cd-pilot-staging.yml`, `cd-production.yml`,
   `generate-client-sdks.yml`), the preflight-fails-loudly rule, immutable
   image digests, expand/contract migrations, and §7's secret list.
2. **The workflow files themselves — blocked.** They were reconstructed in
   `.github/workflows/` from that document, but **any push containing
   `.github/workflows/` is still rejected** because the GitHub connection lacks
   the `workflows` scope (R8). They have **never run on GitHub** and are
   unverified — this is the single open DevOps item a repo admin must fix by
   reconnecting with the `workflows` permission.
3. **`Dockerfile` + `docker-compose.yml`** — compose now brings up
   `db` (Postgres 15), `redis` (7-alpine, health-gated), `migrate`, `api`.
   The API requires Redis in compose (`COUNTER_BACKEND=redis`,
   `REDIS_REQUIRED=true`) and will not start without it. **No Docker daemon is
   available in the sandbox: the image has never been built or run.**
4. **Fail-closed rule (new, verified):** `ENVIRONMENT=production` requires
   `COUNTER_BACKEND=redis`; unreachable Redis ⇒ the app refuses to start. The
   in-memory store is dev/test only and is never a fallback. See
   `src/payday/core/counters.py` and `tests/test_sprint5_shared_infrastructure.py`.
5. **Databases/migrations:** `alembic/versions/` (`001`, `002` — no migration
   was needed for Sprint 5; counters live in Redis). The parity/autogenerate
   drift gate is in the Sprint 4 test suite and in the (unrun) CI
   `migrations-postgres` job.
6. **`.env.example`** — add the new runtime knobs:
   `COUNTER_BACKEND`, `REDIS_URL`, `REDIS_REQUIRED`, `TRUSTED_PROXY_IPS`
   (must be the real load-balancer IPs in front of the API), the
   login/register/refresh rate-limit pairs, and `PIN_FAILURE_*`.

---

## 4. What has been verified — and what has not

| Verified (this session, 2026-09-07) | How |
| --- | --- |
| 121 passed / 2 skipped (up from 100/1) | `pytest -q -p no:logging` |
| No regression in the 101-test baseline | same run, no weakened assertions |
| Login throttle: 5×401 then 429 + `retry-after: 900` | live uvicorn + `curl` |
| Constant-time login (no user enumeration by timing) | `tests/test_sprint5_login_throttling.py` |
| PIN budget shared across two Redis clients (combined 5) | fakeredis store test |
| PIN counter reset on success; fail-closed 503 when store down | `tests/test_sprint5_pin_counter.py` |
| Production refuses to start with memory backend / unreachable Redis | launch checks + live uvicorn exit |
| OpenAPI additions only (429/503 declared; 32 paths) | baseline regen + contract test |
| Sprint 4 work is on `main` (PR #4 merged at `9ac72cf`) | `git log origin/main`, `gh pr view 4` |

| NOT verified (do not claim otherwise) | Why |
| --- | --- |
| CI/CD workflows | `workflows` permission still missing ⇒ never pushed, never ran |
| Docker image | no Docker daemon in the sandbox |
| Production capacity | load test against Postgres is LB-5, still not started (needs D18 + staging) |
| Real SMS delivery | WS-1 blocked on D1; notifications still write `SENT` rows with no outbound call |
| Real Redis in CI | one test skips locally and waits for `PAYDAY_TEST_REDIS_URL` |
