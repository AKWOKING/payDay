# Backend recommendation review — what to change, replace or improve

**Date:** 2026-10-04 · **Author:** Lead System Architect
**Question asked:** "with everything you have just integrated, what will you recommend we
change, replace or improve on our backend?"

**How to read this.** Every recommendation below came from reading the code, not from a
checklist, and each carries the file it lives in, a severity, and an effort estimate. Two
findings were severe enough that leaving them in place while writing a memo would have been
negligent, so they are **already fixed with tests and negative controls** (§1). Everything
else is a recommendation for a decision — none of it has been implemented.

Severity here means *money impact*, consistent with the rest of this repository: 🔴 a path to
lost or invented money, or to full account compromise · 🟠 an outage, audit or support
incident waiting to happen · 🟡 an efficiency or maintainability tax.

---

## 1. Fixed during this review (both 🔴)

### 1.1 The mock simulator was an unauthenticated way to mint balance

**What existed.** `POST /api/v1/mock-telco/{mtn,orange}/simulate-callback` was mounted
unconditionally, took no authentication, and called `transaction_manager.process_webhook`,
which — unlike the operator webhook path — performs **no signature verification and no
operator re-query** and goes straight to `_apply_settlement`, crediting a deposit or
finalising a withdrawal.

**Why that mattered.** A deposit's `external_ref` is returned to the client that created the
transaction (`schemas/transaction.py:114`), so no guessing is needed:

1. call `POST /wallet/deposit` and receive an `external_ref`;
2. never approve the operator's USSD prompt, leaving the transaction `PROCESSING`;
3. `POST /api/v1/mock-telco/mtn/simulate-callback` with that reference and
   `{"status": "SUCCESSFUL"}` — the wallet is credited;
4. withdraw the invented balance to a mobile-money number.

In a production deployment (`TELCO_MODE=live`) that is theft with a curl command. The `403`
for a forged *operator* callback (A8) was correct and irrelevant here: this was a different
door.

**Fix (two independent locks, because one is a routing decision and routing changes):**

| Lock | Where | What it does |
| --- | --- | --- |
| Mounting | `api/v1/router.py` — `simulator_is_mounted()`, `build_api_router()` | The simulator is included only when `TELCO_MODE=mock`. Sandbox is excluded deliberately: A5 exists to verify our integration against the operators' sandboxes, and letting the simulator settle transactions there would quietly invalidate that evidence. |
| Behaviour | `services/transaction_manager.py` — `process_webhook` | Refuses with `403 SIMULATOR_DISABLED` whenever `TELCO_MODE=live`, so a future route cannot re-open the first lock. |

**Evidence:** `tests/test_simulator_lockdown.py` (10 tests) — mount gating for sandbox/live,
mounting preserved for mock, the fully-configured live case, the live refusal with the
transaction asserted to stay `PROCESSING` and `completed_at` untouched, and mock settlement
still working. Negative controls: reverting the mount gate, the live refusal, and the secret
guard each turned the corresponding tests red (6 failures); restoring returned 10 green.

### 1.2 Production would start on the repository's published secrets

**What existed.** `SECRET_KEY` and `ENCRYPTION_KEY` default to constants committed in this
public repository, `docker-compose.yml` passes them through with those same defaults, and
nothing checked them. `SECRET_KEY` signs every access and refresh token.

**Why that mattered.** Anyone who has read the source could mint a token for any account
whose `token_version` is still the default — which is most accounts — and no runtime symptom
would reveal it. The WS-0 precedent (production + Redis unreachable ⇒ refuse to start) exists
for exactly this class of silent, invisible misconfiguration.

**Fix:** `core/config.py` — `validate_security_configuration()`, called from the lifespan
before the app serves traffic. In production it refuses to start while either secret is the
development default or shorter than 32 characters (`MINIMUM_SECRET_LENGTH`; a short HS256
secret is offline-brute-forceable from a single captured token). `DEBUG=true` in production is
a logged warning rather than fatal. Development and CI are untouched.

**Evidence:** the same test file (defaults refused, generated 64-char secrets accepted, a
short non-default secret refused, development never blocked). The message tells the operator
what to do: `openssl rand -hex 32`.

**One note for the contract baseline:** `docs/api/openapi-baseline.json` is generated in mock
mode, so it contains the simulator paths. A live-mode generation legitimately drops them —
generate the baseline in mock mode, as the contract test's docstring already implies.

---

## 2. Recommended next — ranked, not yet done

### 🔴 2.1 Make idempotency keys caller-scoped (small migration, real disclosure path)

`idempotency_key` is looked up **globally**, not for the calling wallet:
`select(Transaction).where(Transaction.idempotency_key == key)` returns the first match and the
handler returns it (`services/transaction_manager.py:150`, and the same pattern in the
withdraw and transfer paths). Two consequences:

* **Cross-account disclosure.** If user B sends a key that user A already used, B receives
  A's transaction object — the response includes `external_ref`, amount, status and masked
  counterparty. Keys are client-generated, so this needs a collision (a frontend bug such as
  a constant key, a retry after an account switch, or a copy-pasted sample), not an attack —
  but that is precisely how it will eventually happen.
* **Silent wrong answer.** A client retrying with a key it already used for a *different*
  operation (a deposit then a transfer, or the same key with a different amount) gets the old
  transaction back and reads it as success. Money did not move.

**Recommendation:** unique constraint on `(wallet_id, idempotency_key)`, lookup scoped to the
wallet, and a `409 IDEMPOTENCY_KEY_REUSED` when the key exists with different parameters
(amount, type, counterparty). One Alembic revision plus the three call sites; the P2P test file
already has helpers that make the regression tests straightforward.

### 🔴 2.2 Backup and restore rehearsal (already tracked as C5)

A ledger without a *tested* restore is one incident away from an unrecoverable loss, and
COBAC/partner audits will ask for both the policy and the evidence. The CD design already
fails closed on a missing snapshot (`docs/CI_CD_PIPELINE_STRATEGY.md`) and C5 in
`docs/MVP_EXECUTION_ROADMAP.md` calls for the rehearsal — it has never been performed. Do it
before real money: automated PITR, a restore into staging, timed and recorded, plus an alert
if the backup hook stops reporting.

### 🟠 2.3 Audit-log integrity (`models/audit_log.py`)

The audit trail is mutable rows in the same database: nothing prevents `UPDATE`/`DELETE`,
`actor_id` is `ON DELETE SET NULL`, and there is no tamper evidence. For a payment institution
this is the record a supervisor or partner bank audits, and CEMAC record-keeping expectations
run to ten years.

**Recommendation:** append-only enforcement at the database level (revoked `UPDATE`/`DELETE`
grants, or a trigger), a hash chain (`prev_hash` + payload hash per row) so tampering is
detectable, and a documented retention/export policy. Cheap now, unconvincing to add later.

### 🟠 2.4 Stop creating schema at startup; check the revision instead

`main.py` calls `Base.metadata.create_all` on every boot, in every environment. With Alembic
in the workflow this is a foot-gun: it can create tables the migrations never did (drift the
migration/model parity tests will not see, because they compare the ORM to the migrations, not
the live database to either).

**Recommendation:** keep `create_all` for local development and tests only, and add a startup
check that `alembic_version` equals the single head — refusing to serve on mismatch, in the
same fail-closed idiom as WS-0. Also assert a single head in CI (`alembic heads`), so two
developers cannot both create revision 006.

### 🟠 2.5 Make CI real, and run it on PostgreSQL (R8, LB-5)

Four workflow files exist locally but have never run — pushes from this environment cannot
include `.github/workflows/*`, so **no green run has ever been observed** (R8). Separately,
the whole suite runs on SQLite with a shared in-memory pool, while production is PostgreSQL
(LB-5): enum handling, `NUMERIC` behaviour, locking and `FOR UPDATE` semantics are exactly
where the two diverge.

**Recommendation:** get the workflows onto the remote (from an environment that can push them,
or via a PR opened in the GitHub UI), then add a PostgreSQL job running the same suite plus
`alembic upgrade head → downgrade base → upgrade head`. Until that is green, treat "tests
pass" as a claim about SQLite.

### 🟠 2.6 SMS delivery (LB-3) — the largest product gap

`notification_service` fabricates a device token; nothing is ever sent. P2P transfers, deposit
confirmations and OTP for password reset all promise a message that will never arrive. This
is D1 (provider) and D3 (sender ID, up to three weeks at MTN) — procurement with a long lead
time, and the OTP leg of LB-1 depends on it.

### 🟠 2.7 KYC document upload (LB-2) with object storage, not the API process

No upload path exists, so identity cannot be verified — and every product limit is gated on
KYC. When it lands: presigned URLs to object storage with D6 (jurisdiction) decided first, per
`docs/design/INFRASTRUCTURE_SCALING_PLAN.md` §4. Streaming documents through the API would be a
DoS surface, a memory bill, and a residency question answered by accident.

### 🟡 2.8 Refresh-token rotation without reuse detection (`services/auth_service.py:142`)

Rotation issues a new refresh token, but the previous one stays valid until it expires unless
`token_version` is bumped (which only happens on revoke-all). A stolen refresh token therefore
survives rotation, and reuse is not detected.

**Recommendation:** give refresh tokens a `jti`, record the current one per session, and on
reuse of a rotated token revoke the whole family (`token_version += 1`). This is the mechanism
that turns "a token leaked" into "we noticed and cut it off".

### 🟡 2.9 Transaction history: keyset pagination + a composite index

History is `ORDER BY created_at DESC OFFSET … LIMIT …` (`api/v1/transactions.py:149`) over an
indexed-on-`wallet_id`-alone table. Offset pagination degrades linearly and can skip or repeat
rows when new transactions arrive between pages — visible to a user scrolling a statement.
Add `(wallet_id, created_at DESC)` and move to keyset (`created_at`/`transaction_id` cursor).

### 🟡 2.10 Operator resilience: one client per call, no retry policy, no brake

Each adapter call constructs a fresh `httpx.AsyncClient` (`mtn_momo.py:319,363,563`;
`orange_money.py:268,317`), so there is no connection reuse; only a stale-token `401` is
retried, and there is no circuit breaker — if an operator degrades, every request waits out
the full timeout while the sweep and callbacks compete for the same capacity.

**Recommendation:** one pooled client per adapter with explicit limits; bounded exponential
backoff with jitter for **read-only** calls (`query_status`, token fetch); a per-operator
circuit breaker; and a "pause channel" admin control (the reconciliation endpoint already shows
the reporting shape) so an operator outage becomes a product decision, not an error storm.
Never blind-retry a write: retries of money movements must carry the same operator reference.

### 🟡 2.11 Rate limits on the money endpoints

Login and PIN are throttled; `deposit`, `withdraw` and `transfer` are not, though the Redis
limiter (`core/ratelimit.py`) is already there and shared. A compromised or buggy client can
burn operator quota, trigger fraud rules, and generate support load at will. Per-account and
per-IP budgets are ~a day of work.

### 🟡 2.12 Metrics and paging (LB-19, second half)

`/admin/ops/overview` made stuck money *visible*; nobody is *notified*. Add a Prometheus text
endpoint, alerts on `stuck_processing.count > 0`, on sweep heartbeat staleness, and on operator
error rate, plus a named recipient (D20). Until then the ops view is a tab someone has to open.

### 🟡 2.13 Password hashing parameters (`core/security.py`)

Bare `bcrypt.gensalt()` with library defaults, no configurable cost, no rehash-on-login path to
a stronger scheme, and no maximum password length (bcrypt silently truncates beyond 72 bytes).
Fine today; make the cost explicit and configurable, add a rehash-on-successful-login upgrade
path (to argon2id when convenient), and cap input length at the validation boundary.

---

## 3. What I recommend *against* (already argued elsewhere)

| Not this | Why | Recorded in |
| --- | --- | --- |
| Microservices, message broker, caching balances, sticky sessions, autoscaling | Premature for our scale; caching money reads is a correctness risk; the split order matters and the ledger goes last | `docs/design/INFRASTRUCTURE_SCALING_PLAN.md` §4–5 |
| A hosted BaaS ("free backend"), generated migrations | Data-residency law 2024/017 + D6; the ledger and audit trail must be ours; migrations must be reviewable and reversible | `docs/research/MATERIALS_REVIEW_2026-10-04.md` §1, §4 |
| Blind retries of money writes | Without the operator's own idempotency reference, a retry is a second payment | §2.10 above |

## 4. Suggested order

1. **Idempotency scoping** (§2.1) — small, and it closes a disclosure/incorrect-success path.
2. **Backup + restore rehearsal** (§2.2) — before any real money, non-negotiable.
3. **CI on PostgreSQL + a green workflow run** (§2.5) — every other claim rests on it.
4. **Audit-log integrity** (§2.3) — required for the licence/partner conversations, cheap now.
5. **SMS (LB-3)** (§2.6) — long-lead procurement; unblocks LB-1's OTP and P2P's promise.
6. Everything 🟡 as capacity allows; the sweep-replica rule (R28) before any second replica.

## 5. Sources

1. `docs/LAUNCH_BLOCKER_ROADMAP.md` — LB-20/LB-21 (this review), LB-19, LB-3, LB-5, R8, WS-0.
2. `docs/design/INFRASTRUCTURE_SCALING_PLAN.md` — refusals, triggers, R28–R30.
3. `docs/research/MATERIALS_REVIEW_2026-10-04.md` — BaaS and generated-migration rejections.
4. `docs/MVP_EXECUTION_ROADMAP.md` C5 — backup and restore rehearsal.
5. `docs/research/API_ACCESS_MTN_OM_CAMEROON.md` — licensing route that the audit trail and
   data-residency decisions serve.
