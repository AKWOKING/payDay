# Infrastructure & scaling plan — what we adopt, what we refuse, and the trigger that changes our mind

**Date:** 2026-10-04 · **Author:** Lead System Architect
**Source:** *Fundamentals of Backend Architecture — How to Design Scalable Software*,
Tiago Taquelim (2026-06-08, 48 min) — https://youtu.be/Qa-7iWxDz1A

**Retrieval note (honesty about evidence).** The transcript was readable up to the
file-serving section (~27 min): architecture definition, stateful vs stateless servers,
load balancing (round robin, weighted, sticky sessions, health checks, path routing, an
Nginx example), horizontal vs vertical scaling and autoscaling, microservices with an API
gateway and edge authentication, and file serving through object storage. YouTube served
only page furniture for the remaining sections (event broker, caching/CDN, rate limiting),
so **those three are not attributed to the video's specific arguments below** — they are
covered from their stated topics plus our own analysis, and are marked as such.

The video's own thesis is the reason this document is mostly a list of things we are
**not** doing yet: architecture is a sequence of stage-dependent decisions, and the
failure mode is adopting the architecture of a big company before being one.

---

## 1. Where we actually are

```
            ┌──────────────────────────────────────────────┐
 clients ──▶│  one FastAPI process (N replicas possible)   │
            │  stateless: no sessions, no money, no caches │
            └───────┬───────────────────────┬──────────────┘
                    │                       │
             PostgreSQL (ledger)      Redis (throttles, PIN lockout)
```

One deployable, one primary datastore, one shared counter store, started by a compose
stack (`docker-compose.yml`: db → redis → migrate → api). At pilot scale this is correct,
and the video's progression — server, then database, then load balancer, then services —
says the same thing: the database step exists to make the server stateless, and the
load-balancer step exists to make that statelessness useful. We are at "database +
stateless server" and have just made the load-balancer step *possible*, not necessary.

## 2. Concept-by-concept audit

| Video concept | What the video argues | What we have today | Verdict |
| --- | --- | --- | --- |
| **Stateless servers** | Data coupled to the machine means no scale and no resilience; move state to the database, then treat app instances as disposable | Login throttle, PIN lockout and OTP counters are in Redis (`core/counters.py`, `core/ratelimit.py`); idempotency keys are rows in the DB; tokens are JWTs plus `users.token_version`; no in-process caches. Tests never assert on process-local state | ✅ Adopted — this is the property that makes everything below optional |
| **Load balancing** | A load balancer routes on health, so health signals must be able to say "no" | **Was broken**: one `/health` endpoint that always answered 200, even with Redis unreachable — an orchestrator reads the status code, not the body, so a broken instance kept taking traffic. Now: `/public/health/live` (restart me — touches nothing) and `/public/health/ready` (**503** when the DB or counter store is down). Compose healthcheck switched to `ready` | ✅ Adopted (this increment) |
| **Sticky sessions** | Needed when an instance holds per-user state | Not needed. We hold none — and the counter store is keyed by account/IP, not by instance, so round-robin is safe. Avoiding stickiness is what lets us scale down to zero and replace instances freely | ✅ Adopted by omission — recorded so nobody adds session affinity "for safety" |
| **Horizontal vs vertical scaling** | Both are legitimate; horizontal needs the state problem solved first | Compose targets PostgreSQL. **SQLite is dev/test only — it is single-writer** and cannot back two replicas. Vertical scaling is the first lever (cheap, no architectural change) | 🟡 Deferred with a rule: no horizontal capacity claim without the §4 checklist (LB-5) |
| **Microservices + API gateway** | Extract services when a *business* reason exists (independent scaling, team ownership); put one gateway in front, keep services on a private network, verify JWTs at the edge | One deployable today, which the video agrees is right until the business justifies otherwise. We already have the gateway's *function*: a single entry point, path-based routing, edge JWT verification in dependencies, 401 before any handler | ✅ Adopted as a decision **not** to split yet — extraction triggers in §4 |
| **Authentication at the edge** | Verify the token at the gateway without a network round trip | JWT is verified per request, but we then load the user and check `token_version` (revocation) — a DB read per authenticated request. That is deliberate: LB-1/LB-7 require revocation to be immediate, not eventually-consistent | ⚠️ Documented tradeoff — cache `token_version` only with an explicit staleness budget, never on money writes |
| **File serving** | Never stream files through the app or into the relational DB; store objects, hand out direct upload/download links | No user file uploads yet. **When KYC document upload lands (WS-6 / LB-2) it must not go through the API process**: object storage + presigned URLs, with the storage-jurisdiction decision (D6) made first | 🟡 Rule for the next increment |
| **Event broker** | Decouple work that need not happen inside the request | Notifications are in-process; the A8 status sweep recovers lost operator callbacks. `services/task_queue.py` is in-memory — a restart loses pending work, and two replicas would each run their own copy | 🟡 Deferred, with triggers in §4 (this is the first thing to externalise) |
| **Caching & CDNs** | Cache reads close to the user; put static assets on a CDN | **Caching is a correctness risk here, not an optimisation.** A cached balance is a wrong answer that looks right. Default is now `Cache-Control: no-store` on every `/api/` response, including errors, with per-route opt-in for anything genuinely cacheable | ✅ Adopted as a safety default (this increment) |
| **Rate limiting** | Limits belong at the edge and are cheapest there | We have app-level, Redis-backed, per-account limits (login, PIN, and the route limiter) — the edge *cannot* key on an account ID. Edge limiting (Nginx/cloud WAF) is additive defence against volumetric abuse, never a replacement | ⚠️ Layered, not either/or |

## 3. Invariants (these must survive any scaling decision)

1. **No money or session state in the app process.** Redis or PostgreSQL only.
2. **Probes must be able to fail.** Liveness never depends on a shared dependency;
   readiness depends on all of them.
3. **No API response is cacheable.** Enforced centrally, tested.
4. **One writer per wallet per transaction.** Row locks in the money path, asserted by
   the conservation tests.
5. **Background work must be idempotent, and single-runner where it mutates state.**
6. **Callbacks are signed, verified, idempotent — and never cached.**
7. **SQLite is not a deployment target.** Multi-replica means PostgreSQL.

## 4. Triggers — the signal that retires a "not yet"

| Signal | Action | Why then, not now |
| --- | --- | --- |
| Sustained p95 near the agreed SLO (LB-5 numbers), or DB connections saturating on one instance | Scale vertically first, then add PgBouncer | Cheapest lever; a pooler beats replicas for connection churn |
| A second API replica is genuinely needed (uptime or throughput) | **Run the multi-replica checklist**: PostgreSQL ✓, probes ✓, no process-local state, and **elect a single runner for the A8 status sweep** (Redis lock) — today every replica would sweep the same rows and could double-notify | The sweep is correct but not replica-aware; a duplicate runner is a customer-visible defect (duplicate SMS), not a small inefficiency |
| Notifications must survive a restart, or fan out to SMS + push + email | Externalise `services/task_queue.py` (Redis Streams or Celery) with a DB outbox, so a send is a durable intent rather than an in-process task | In-process is fine while delivery is best-effort and single-instance |
| KYC document upload goes live (WS-6 / LB-2) | Object storage + presigned direct upload; retention and jurisdiction (D6) decided before the first document is accepted | Uploads through the API are a DoS surface and a memory bill; rewiring later means migrating customer documents |
| Volumetric abuse or credential-stuffing against `/auth/login` | Edge rate limiting + provider WAF in front of the app-level account limits | App limits already cover accounts; the edge covers the traffic that never authenticates |
| More than one feature team, or deploys that must not ship together | Consider extracting **one** service (candidates in order: notifications, operator adapters/webhooks, reporting; the ledger last) | Splitting the ledger first would trade a transaction boundary for a distributed-consistency problem — the explicit anti-goal |

## 5. Explicitly refused for now (with the reason and the revisit condition)

- **Microservices** — a distributed system with one team's worth of traffic. Revisit: an
  extraction trigger above fires.
- **Message broker / Kafka** — no consumer needs durability or fan-out yet. Revisit: the
  outbox trigger.
- **Caching money reads** (Redis or CDN) — a stale balance is a correctness bug that no
  cache-invalidation policy makes safe enough. Revisit: never for balances; per-route only
  for static catalogue data.
- **Sticky sessions** — we would be re-introducing state we spent WS-0/WS-3 removing.
- **Autoscaling and multi-region** — pre-revenue capacity spent on nothing. Revisit:
  LB-5 data and the pilot's actual shape.
- **A separate API gateway product** — FastAPI already is the single entry point. A
  reverse proxy (TLS, compression, request IDs, edge limits) is a deployment choice for
  staging, not an architectural change.

## 6. Residuals

| # | Residual | Status |
| --- | --- | --- |
| R28 | **The A8 status sweep is not replica-aware.** Two replicas both sweep and may both apply/notify a transition. Correct on one instance; a prerequisite before scaling out | Accepted, bounded by the §4 checklist. Transition application is idempotent, so the risk is duplicate work/notifications, not double-crediting |
| R29 | **`services/task_queue.py` is process-local** (in-memory dict). Work in flight is lost on restart and invisible to other replicas | Accepted while notification delivery is best-effort; see the externalisation trigger |
| R30 | **`token_version` is read from the DB on every authenticated request**, so auth is a per-request read. At pilot volume this is cheap and it keeps revocation immediate | Accepted deliberately (LB-1/LB-7). A short-TTL cache would weaken revocation — decision required before any such cache is added |

## 7. Test-harness defect found and fixed while integrating this

The load-capacity suite failed **depending on test order**: `test_no_request_is_dropped_under_burst`
failed with `RuntimeError: <asyncio.locks.Lock object ...> is bound to a different event loop`.
Root cause: the test engine is a module-global SQLite `StaticPool` (one shared DBAPI
connection) while pytest-asyncio created a **fresh event loop per test**; `aiosqlite` binds
that connection's lock to the loop that first used it, so any subsequent *concurrent* test
collided with a lock owned by a dead loop. A suite whose result depends on order cannot
support any claim we make about it.

**Fix:** one session-scoped event loop for tests (`asyncio_default_test_loop_scope` and
`asyncio_default_fixture_loop_scope = "session"` in `pyproject.toml`) — which also matches
production, where the app runs in a single loop for the life of the process.

**Evidence.** Before: the pair ran 3× with the fix's source files present → `1 failed,
1 passed` each time; and 3× with those source files stashed (`git stash`) → `1 failed,
1 passed` each time, i.e. **the failure was independent of this increment's code**. After:
the pair passed 3/3, and the full suite is **238 passed, 2 skipped**.

## 8. What this increment changed in code

| Change | Where | Test |
| --- | --- | --- |
| `/public/health/live` (no dependencies) and `/public/health/ready` (DB + counter store, **503** when down; each dependency reported independently) | `api/v1/public.py` | `tests/test_infra_probes_and_caching.py` (5 tests, including both failure modes and an injected raising store) |
| `Cache-Control: no-store` default on all `/api/` responses, errors included, per-route opt-in | `main.py` middleware | same file (3 tests: public path, authenticated money reads, 401) |
| Compose api healthcheck now gates on `ready` so an orchestrator can act | `docker-compose.yml` | Not exercisable in this sandbox — recorded as unverified config, like all compose changes |
| Contract baseline regenerated: 34 → **36 paths** | `docs/api/openapi-baseline.json` | contract drift 10 passed |

Both probes are covered in the OpenAPI baseline and listed in the contract test's
`PUBLIC_PATHS` (load balancers hold no JWT). Negative controls: forcing readiness to
always answer 200 and deleting the middleware each turned the corresponding tests red
(6 failures), and restoring them returned 9 green.

## 9. Sources

1. Tiago Taquelim, *Fundamentals of Backend Architecture — How to Design Scalable
   Software*, 2026-06-08 — https://youtu.be/Qa-7iWxDz1A (sections retrieved: 0:00–27:14).
   Architecture-design diagram referenced in its description:
   https://excalidraw.com/#json=eclZxZVraNTEYC73Z481H,fNzwIWAT85XtfPi2uFXXKw
2. Martin Fowler, *Software Architecture* — https://martinfowler.com/architecture/
   (the "good architecture makes new capabilities cheaper to add" framing the video opens
   with, and which is why this document lists refusals as deliberately as adoptions).
3. Sections 32:30–48:11 (event broker, caching/CDN, rate limiting): transcript not
   retrievable by the research tooling; treated as topics, not attributed claims.
