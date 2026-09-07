# Sprint 5 — Redis Counter Store, Login Throttling, Shared PIN Lockout

**Date:** 2026-09-07 · **Scope:** `docs/LAUNCH_BLOCKER_ROADMAP.md` WS-0, WS-3, WS-5
**Verdict:** implemented and tested; two documented follow-ups remain (owner
notification on throttle breach — blocked on WS-1/D1; CI run of the Redis
integration test — blocked on the GitHub `workflows` permission, R8).

---

## 1. Why this sprint

Three launch blockers shared one root cause: **all shared mutable state was a
process-local dict**. `POST /auth/login` was completely unthrottled, and the
5-attempt PIN lockout was per-process, so N replicas gave an attacker 5N
attempts. The roadmap's recommended order was WS-0 → WS-3 + WS-5, since none of
them need a product decision beyond D21 (Redis approved). This report documents
that build.

## 2. What was built

### 2.1 Shared counter store — `src/payday/core/counters.py`

A single `CounterStore` abstraction with two implementations and no fallback:

- `RedisCounterStore` — atomic `INCR` + `EXPIRE` + `TTL` over `redis.asyncio`
  (pipeline on one connection). The production backend.
- `MemoryCounterStore` — process-local, explicit dev/test backend only,
  selected by `COUNTER_BACKEND=memory` (the default for local runs). It is
  **never** selected as a fallback when Redis is down.

Selection is `COUNTER_BACKEND` (`memory` | `redis`). The singleton lives behind
`get_counter_store()`; tests rebuild it between cases.

### 2.2 Redis client — `src/payday/core/redis_client.py`

Lazy process-wide `redis.asyncio` client from `REDIS_URL`, `close_redis()` on
shutdown, and a best-effort ping helper for the health endpoint.

### 2.3 Fail-closed startup — `ensure_counters_ready()`

Called from the FastAPI lifespan **before** serving traffic:

- `ENVIRONMENT=production` with `COUNTER_BACKEND=memory` → `RuntimeError`,
  app refuses to start.
- `COUNTER_BACKEND=redis` and Redis unreachable → `RuntimeError`, app refuses
  to start.
- `REDIS_REQUIRED=true` with the memory backend → `RuntimeError`.

`validate_counter_configuration()` is the pure config check; a short-lived
connection (`check_redis_connection()`) does the liveness probe.

### 2.4 Rate limiter — `src/payday/core/ratelimit.py`

`RateLimiter` over the counter store: `hit(key, limit, window)` increments and
returns allow/count/remaining/`retry_after`; `enforce_rate_limit()` raises the
new `RateLimitError` (429, `RATE_LIMITED`, `Retry-After` header — RFC 7807
envelope) when over threshold.

IP resolution (`get_client_ip`) honours `X-Forwarded-For` **only** when the
socket peer is in `TRUSTED_PROXY_IPS`; otherwise the header is attacker-
controlled and ignored.

### 2.5 WS-3 — `POST /auth/login` throttling

- Per-phone: `LOGIN_RATE_LIMIT_PHONE=5` / `LOGIN_RATE_LIMIT_PHONE_WINDOW=900` (15 min).
- Per-IP: `LOGIN_RATE_LIMIT_IP=20` / `LOGIN_RATE_LIMIT_IP_WINDOW=900`.
- A **successful** login resets the per-phone counter; the per-IP bucket stays
  windowed.
- The same limiter is applied to `POST /auth/register` (per-IP, 10/h — no
  per-phone bucket, so duplicate registrations still return 409) and
  `POST /auth/refresh` (per-IP, 10/15 min).
- **Constant-time login:** `AuthService.login_user` now runs a real bcrypt
  comparison against a dummy hash when the phone number does not exist, so the
  response time no longer reveals account existence (user-enumeration oracle
  removed). See `_dummy_password_hash()` in `auth_service.py`.

### 2.6 WS-5 — PIN counter moved to the shared store

`TransactionManager._failed_pin_attempts` (process-local dict) is gone. The
freeze path now:

1. `INCR pin:fail:{user_id}` on the shared store with
   `PIN_FAILURE_TTL_SECONDS=86400` (24 h default — D-extra recommendation).
2. At `PIN_FAILURE_LIMIT=5` the existing freeze flow runs unchanged
   (wallet → `FROZEN`, audit `WALLET_AUTO_FROZEN_PIN_BRUTE_FORCE`, security
   notification, `WalletFrozenError`).
3. A correct PIN **deletes** the counter.
4. If the store is unreachable the attempt raises `RedisUnavailableError`
   (503, `REDIS_UNAVAILABLE`) — the transaction is refused, never allowed
   unproven.

### 2.7 Config, compose, contract

- `config.py`: `REDIS_URL`, `REDIS_REQUIRED`, `COUNTER_BACKEND`,
  `TRUSTED_PROXY_IPS`, the login/register/refresh limit pairs,
  `PIN_FAILURE_LIMIT`, `PIN_FAILURE_TTL_SECONDS`.
- `.env.example`: documented with the fail-closed rule.
- `docker-compose.yml`: added `redis:7-alpine` (health-gated); `api` depends on
  it and runs `COUNTER_BACKEND=redis`, `REDIS_URL=redis://redis:6379/0`,
  `REDIS_REQUIRED=true`. The old "no broker because nothing talks to it"
  comment is deleted.
- `pyproject.toml`: `redis>=5.0.0` (runtime), `fakeredis>=2.20.0` (dev).
- `core/exceptions.py`: `RateLimitError` (429) and `RedisUnavailableError` (503).
- `main.py`: 429/503 are now declared in the OpenAPI error responses (baseline
  regenerated — additions only, no removals), and `/public/health` reports
  `counter_backend` + `redis.status`.

## 3. Tests

New suites (`tests/test_sprint5_*`), 21 tests:

- **shared_infrastructure** — allow-N/reject-N+1, recovery after the window
  (deterministic clock), shared budget across two limiter instances, shared
  budget across two **Redis clients** over one fakeredis server (the LB-4
  shape), `RateLimitError` shape, production/memory rejection,
  `REDIS_REQUIRED`/memory rejection, unreachable-Redis startup gate,
  health reporting, plus a real-Redis integration test that **skips locally**
  and runs in CI when `PAYDAY_TEST_REDIS_URL` is set.
- **login_throttling** — 5 failures then 429 + `Retry-After` + RFC 7807 body;
  success resets the phone counter; per-IP limit independent of account; register
  and refresh throttled; `X-Forwarded-For` only trusted from configured proxies;
  unknown-user vs wrong-password timing within an order of magnitude.
- **pin_counter** — combined budget across two store instances; reset-on-success
  via the public withdrawal flow; fail-closed 503 when the store raises.

**Result run 2026-09-07 (SQLite in-memory, no Redis binary in the sandbox):**

```
121 passed, 2 skipped, 1 warning in 68.60s
```

Baseline before this sprint: 100 passed, 1 skipped. The extra skip is the
real-Redis integration test (no `PAYDAY_TEST_REDIS_URL` locally); the
pre-existing skip is unchanged. Existing Sprint 3 brute-force, Sprint 2
withdrawal and Sprint 4 tests pass unchanged (no weakened assertions).

## 4. Live verification (uvicorn, development config)

- `GET /api/v1/public/health` → `status: "UP"`, `redis.status: "UP"`,
  `counter_backend: "memory"`.
- 6 bad logins against one account → `401, 401, 401, 401, 401, 429`; the 429
  response carries `retry-after: 900` and `code: "RATE_LIMITED"`.
- `ENVIRONMENT=production COUNTER_BACKEND=memory` → uvicorn exits with
  `RuntimeError ... Refusing to start (fail-closed)`.
- `COUNTER_BACKEND=redis REDIS_URL=redis://127.0.0.1:1/0` → uvicorn exits with
  `RuntimeError ... Redis is required ... but unreachable`.

## 5. Not done — deliberately

| Item | Why |
| --- | --- |
| WS-3 deliverable 3: notify the account owner on throttle breach | Reuses WS-1 notification delivery, which is blocked on D1 (SMS aggregator). The brute-force alert for the **PIN** path already existed and still fires. |
| CI run of the Redis-integration tests + all workflows | `.github/workflows/` cannot be pushed until the GitHub `workflows` permission is restored (R8). `ci.yml` is written to run `redis:7` and set `PAYDAY_TEST_REDIS_URL`; it has **never run on GitHub**. |
| D-extra (PIN TTL) | 24 h default is in place as the roadmap recommended; product confirmation still open. |
| D21 (Redis approved) | Code is production-ready behind `COUNTER_BACKEND=redis`; a provisioned, authenticated Redis instance is still DevOps/product input. |
| Celery | Still aspirational — `task_queue.py` remains in-process (unchanged, documented in handover §9.2). |

## 6. How to run the Redis-backed configuration

```bash
# local stack (compose uses COUNTER_BACKEND=redis + redis service)
docker compose up --build

# suite with the real-Redis integration tests against a local server
PAYDAY_TEST_REDIS_URL=redis://127.0.0.1:6379/0 .venv/bin/python -m pytest \
  -q -p no:logging tests/test_sprint5_shared_infrastructure.py

# production settings (fail-closed if Redis is unreachable)
ENVIRONMENT=production COUNTER_BACKEND=redis REDIS_URL=redis://...:6379/0 \
REDIS_REQUIRED=true uvicorn payday.main:app --host 0.0.0.0 --port 8000
```
