# PayDay — Target System Architecture

**Date:** 2026-09-17 · **Author:** Lead System Architect
**Backend state:** commit `a15e221` (M1 A1–A8 shipped) · **Test baseline:** 205 passed, 2 skipped
**Companions:** `docs/MVP_EXECUTION_ROADMAP.md` (order of work), `docs/LAUNCH_BLOCKER_ROADMAP.md`
(defect register), `docs/reports/FRONTEND_REVIEW_2026-09-17.md` (frontend claim audit)

---

## 1. How to read this

This is the architecture PayDay **should** have, derived from three inputs: what the
code actually does today (re-verified, not assumed), what the frontend has promised
customers, and what a payment institution in CEMAC has to be able to prove.

It is deliberately split into:

* **What exists** (§2) — verifiable in the repository. No aspiration.
* **What has to be** (§3–§13) — the target, with the reasoning that constrains it.
* **What is needed from others** (§14) — decisions, credentials and inputs I cannot
  generate. This is the part that unblocks work.

The design principle throughout: **money must be provable.** Every franc that moves
needs a row that says why, a counterpart that nets it out, an outsider's answer that
authorises it, and a human who can find it three years later.

---

## 2. What exists today (verified)

**Runtime.** One FastAPI application (`src/payday/main.py`) with a lifespan that
validates configuration before serving, an async SQLAlchemy layer (Postgres in
production, SQLite in tests), Redis-backed shared counters, and a deliberately
in-process mock telco adapter. There is **no worker process, no scheduler, no
message bus, and no object storage**.

**Modules.** `api/v1/*` (33 published paths), `services/*` (wallet engine,
transaction manager, auth, KYC, notifications, audit, reconciliation, status sweep),
`adapters/*` (MTN, Orange, factory), `core/*` (config, money, msisdn, security,
counters, ratelimit, redis), `models/*`, `alembic/versions/001–004`.

**Money movement.** Deposits and withdrawals against MTN/Orange; wallet holds for
withdrawals; whole-franc enforcement at the API and ledger boundaries (`core/money.py`);
operator settlement decided only by the operator's own status answer (A8); a periodic
status sweep for notifications that never arrive.

**Controls that exist.** JWT access/refresh with a token-version revocation counter,
login + PIN throttling through a shared counter store, PIN brute-force auto-freeze,
admin RBAC, audit log, PII field encryption, CORS allowlist, RFC 7807 errors,
idempotency keys on deposits and withdrawals.

**Controls that are declared but not wired** (these are defects, not gaps in ambition):

| Declared | Actual |
| --- | --- |
| `wallet.monthly_limit` is set by admins and returned to clients | **Never enforced** anywhere. `MonthlyLimitExceededError` is never raised. |
| `KycRequiredError`, `get_current_verified_user` | **Never used.** An unverified user can deposit and withdraw. |
| A maximum wallet balance (published by the frontend as 5,000,000 XAF) | **Does not exist.** No credit path has a ceiling. |
| Bills, agents, merchant accounts, P2P, bank linking, mobile app, USSD freeze | Not implemented. |

---

## 3. Context and actors

```
        ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
        │ Customer app │   │  Agent app    │   │ Admin console│
        │ (Flutter/Web)│   │  (future)     │   │ (Angular)    │
        └──────┬───────┘   └──────┬────────┘   └──────┬───────┘
               │                  │                   │
               └──────────┬───────┴───────────────────┘
                          │  HTTPS, JWT, RFC 7807
                 ┌────────▼─────────┐
                 │   PayDay API     │  ← the only writer of balances
                 └──┬────┬────┬─────┘
        ┌───────────┘    │    └────────────┐
        │                │                 │
 ┌──────▼──────┐  ┌──────▼──────┐  ┌───────▼────────┐
 │ MTN MoMo    │  │ Orange Money│  │ Compliance &   │
 │ (collect,   │  │ (webpay,    │  │ infra services │
 │  disburse,  │  │  payout,    │  │ SMS · KYC/IDV  │
 │  callbacks) │  │  callbacks) │  │ bank · storage │
 └─────────────┘  └─────────────┘  └────────────────┘
```

Actors the architecture must eventually serve, even where they are not built:
**the customer** (send, receive, cash out, pay bills), **the agent** (cash-in/cash-out
on a float), **the merchant** (accept, settle), **the operator** (MTN/Orange,
asynchronous and untrusted-by-default), **the regulator/auditor** (who reads records
written by someone who no longer works here), and **support** (who must answer "where
is my money" without a developer).

---

## 4. Container / runtime architecture (target)

| Component | Role | Runs where | Why separate |
| --- | --- | --- | --- |
| **API** (N replicas) | All client traffic, money initiation, callback ingress | Container, behind TLS LB | Scales with app traffic; stateless apart from Redis |
| **Settlement worker** | Consumes a durable job queue: requeries, retries, receipts, notification dispatch | 1+ replica | Long/retrying work must not hold an HTTP request; today it is inline and therefore lossy |
| **Scheduler** | Single-owner cron: status sweep, reconciliation, settlement files, statement generation | 1 replica with leader lock | Must run exactly once per interval; the in-process asyncio task in `status_sweep.py` cannot do that |
| **Postgres** | System of record: users, wallets, transactions, audit | Managed, PITR | The ledger |
| **Redis** | Shared counters, rate limits, idempotency cache, job queue | Managed, HA | Already required fail-closed for shared state |
| **Object storage** | KYC documents, receipts, settlement exports | Region-pinned | Residency requirement (Cameroon Law 2024/017) |
| **Secrets manager** | Operator keys, JWT keys, DB creds, SMS keys | Cloud KMS-backed | Credentials currently live in `.env` files |

**Why the worker matters now, not later:** the A9 sweep is started as an in-process
asyncio task in the API lifespan. Two API replicas means two sweeps (harmless — it is
idempotent — but wasteful), and an API restart mid-pass loses the pass. More
importantly, when SMS and DLR land (WS-1), retrying an SMS cannot be done inline in a
request that has already returned. Establishing the queue *before* the fourth
inline side-effect is cheaper than extracting it afterwards.

---

## 5. Money architecture

### 5.1 Invariants (must hold after every request, and be asserted by tests)

1. **Conservation.** For any transfer group, `Σ debits = Σ credits`. Between
   wallets, money is moved, never created or destroyed.
2. **Non-negative.** `balance ≥ 0` and `locked_balance ≥ 0` (already DB check
   constraints, so a bug is a failed write rather than a wrong balance).
3. **Single writer.** Only `WalletEngine` mutates a balance; only
   `TransactionManager._apply_settlement` finalises a transaction.
4. **Whole francs.** XAF has no minor unit. Amounts are whole numbers at every
   boundary — API validation, ledger arithmetic, operator payload (`core/money.py`).
5. **Every movement is attributable.** A transaction row with an idempotency key,
   an actor, an audit entry, and (for operator money) the operator's own reference.
6. **Nothing finalises on unverified input.** Callback bodies are hints; settlement
   requires the operator's status answer (A8).

### 5.2 Ledger model

The current single-entry-per-wallet model is **sufficient and stays**, with two
additions that internal transfers force:

* **`direction`** (`CREDIT`/`DEBIT`, NOT NULL, backfilled) — because once money can
  move between two of our own wallets, the sign of a movement is no longer derivable
  from `type` alone, and because the frontend renders signed amounts.
* **`transfer_group_id` + `counterparty_wallet_id` + masked counterparty MSISDN** —
  so the two legs are provably one movement (conservation checks group by
  `transfer_group_id`), and so a statement can say "to 2376•••233" without exposing a
  full number in a list response.

Full double-entry (a journal with a liability account per wallet) is the textbook
answer and is **not** justified yet: the platform is not a bank, funds are not lent,
and every movement is already paired internally. The trigger to revisit is merchant
settlement or agent float, where third-party balances appear. Recorded as D-30.

### 5.3 The send path (one endpoint, two realities)

"Send money to anyone in Cameroon" is a single user action with two backends:

```
POST /wallet/transfer  {recipient_phone, amount, pin, channel?, note?}
   │
   ├─ recipient is a PayDay user → INTERNAL: atomic ledger move, instant, free
   │                                (two legs, one DB transaction, same group id)
   │
   └─ recipient is not (or channel supplied) → EXTERNAL: delegate to the existing
        withdrawal path (hold → MTN transfer / Orange payout → operator confirms)
        fee 1.0%, asynchronous, settled on the operator's answer
```

Why one endpoint: the customer's intent is one thing, and asking the app to decide
which reality it is would duplicate a routing rule in every client. The response tells
the client what happened (`internal: true/false`, direction, status), so the UI can
render "sent instantly" or "processing with MTN" without a second round trip.

Risk, stated: if recipient resolution is ever wrong (two users, one number), money
moves to the wrong wallet. Mitigation: resolution is by the *unique* registered phone
number, is channel-agnostic, and a mismatch is a support-visible event, not a silent
one — the recipient leg is visible to both parties immediately.

### 5.4 Limits and ceilings (target)

| Control | Scope | State |
| --- | --- | --- |
| Per-transaction max | amount + fee vs available balance | exists |
| **Daily outgoing** | sums DEBIT movements (withdraw + transfer + bills) over 24h | exists for withdrawals only; must include transfers |
| **Monthly outgoing** | same over the calendar month | **missing — build now** |
| **Incoming ceiling** (max balance) | any credit path: deposit, transfer-in | **missing — build now** |
| Velocity | distinct recipients per hour, structuring detection | not built — AML track |
| KYC tier | unverified vs verified ceilings | not built — AML track |

The published promises must be derived from this table, not typed into a marketing
page: the frontend currently publishes 1,000,000/day against an enforced 500,000, and
a 5,000,000 balance ceiling that does not exist.

### 5.5 Reconciliation

Three layers, only two of which exist:

1. **Per-transaction** — the operator's status answer is the truth (A8). ✅
2. **Sweep** — every PROCESSING transaction is re-queried until the operator answers
   (A9, in `status_sweep.py`, currently in-process). ✅ with the caveat above.
3. **Settlement-file reconciliation** — daily operator/settlement files matched
   against our ledger, breaking on unmatched references, and proving conservation
   across transfer groups. ⏳ This is the control an auditor asks for first; the
   existing `reconciliation_service` is admin-triggered and synthetic-feed based.

---

## 6. Trust boundaries and security architecture

| Boundary | Control | State |
| --- | --- | --- |
| Client → API | JWT + token version, per-endpoint throttling, RFC 7807 errors | ✅ |
| PIN authorisation | PIN hash, shared-counter lockout, auto-freeze at 5 | ✅ |
| **KYC gating** | `KYC_REQUIRED` before money leaves the platform | **declared, unused — build now** |
| Operator → API (callbacks) | parse per operator, match, `notif_token` compare, requery, amount cross-check | ✅ (A8) |
| API → Operator | per-product credentials, token cache with expiry, fail-closed config | ✅ |
| Secrets | currently `.env`; target secrets manager + rotation | ⏳ (M2/C-track) |
| PII at rest | AES-256-GCM field encryption for ID numbers | ✅ |
| Data residency | documents + backups in-region, DPA obligation | ⏳ decision D6/D8 |

**Rule that must survive every future change:** any new inbound integration gets the
A8 treatment — authenticate what can be authenticated, treat the rest as a hint, and
let only a verified, idempotent, audited path move money.

---

## 7. Operator integration architecture

Three modes (`TELCO_MODE=mock|sandbox|live`) with fail-closed startup, per-product
credentials for MTN, separately issued Orange auth, configurable status/endpoint
suffixes where the vendors' APIs are ambiguous, and a documented promotion path:
mock → sandbox (A5) → live.

**The gap the architecture must name:** everything in §"operator" is pinned by tests
against *documentation*, not against an operator. Until A5 runs, the honest status is
"correctly built, externally unverified". The design makes A5 cheap (config-driven
endpoints, a sandbox that differs only by configuration) precisely because it cannot
make it unnecessary.

---

## 8. Frontend contract architecture

The frontend is a first-class consumer and needs a contract it can build against
without reading our source:

* **Envelope** — `{success, message, data}` and RFC 7807 errors with a stable `code`.
  Already published; must not drift (there is a contract-drift test).
* **Money** — whole francs, JSON numbers, explicit `currency`; documented once.
* **Lists** — `page`, `page_size`, `total`, `total_pages`; filters named
  `tx_type`/`tx_status`/`channel`. Already correct after the naming fix.
* **Signed amounts and parties** — `direction` and counterparty fields so history rows
  render `+450,000` / `−14,900` and "to 2376•••233" from one response.
* **Truthful state, not invented timing** — the client must be able to say what is
  happening and what happens next. Target: each transaction carries an
  `expected_resolution`/`next_check` hint derived from the sweep configuration, so no
  client hard-codes "15 minutes".
* **Schema drift protection** — `docs/api/openapi-baseline.json` is the contract of
  record, diffed by a test; client SDKs can be generated from it (the `generate-client-sdks`
  workflow exists but has never run — R8).
* **Versioning** — path-versioned `/api/v1`; additive changes only within a version;
  a breaking change means `/api/v2` plus a deprecation window.

---

## 9. Notification architecture (target)

Today: rows written directly as `SENT` with no transport at all — an honest lie the
notification service tells itself. Target: an **outbox** pattern — the domain writes
an intent (`PENDING`), the worker claims it, a transport adapter sends it, and a DLR
callback (or provider response) moves it to `SENT`/`DELIVERED`/`FAILED` with retries
and backoff. The frontend's notification feed must show real delivery state, and
"support in English and French" means locale on the user, not a hard-coded string.
That is WS-1/LB-3, and it is upstream of OTP for password reset (D1/D3 procurement).

---

## 10. Data architecture

* **Numbering** — one linear migration chain, one migration per change, numbered in
  the order they land: 001–004 today; transfers take **005**, so WS-1 and WS-6 become
  **006/007**. Parity between models and migrations is enforced by a test that diffs
  `Base.metadata` against a migrated database.
* **Retention** — audit and financial records: 10 years (CEMAC Reg 02/24). Requires a
  storage tier decision plus backup PITR (M2).
* **PII** — ID numbers encrypted; documents in object storage with strip-EXIF +
  magic-byte validation (WS-6); residency per D6.
* **Reversibility** — every migration has a `downgrade()`; data-destructive changes
  need a two-phase deploy.

---

## 11. Non-functional targets

| Attribute | Target | Basis |
| --- | --- | --- |
| Internal transfer latency | p95 < 500 ms (ledger-only, no operator) | Two row locks and two inserts; measured by the WS-7 load test |
| API availability | 99.5% monthly during pilot | Single region, managed Postgres |
| Operator calls | Never block a customer request beyond the configured timeout (15s) | `TELCO_HTTP_TIMEOUT_SECONDS` |
| Recovery | RPO ≤ 15 min, RTO ≤ 4 h | PITR + tested restore (M2 exit criterion) |
| Observability | Structured logs, request IDs, per-operator success rate, sweep lag, outbox depth, alerting to a named human | M2 |
| Capacity claims | **None until WS-7 runs on deployed staging** | Standing rule |

---

## 12. Environments and delivery

`local (mock) → staging (sandbox operators, Postgres, Redis, real SMS) → production (live)`.
Promotion is by migration + config change, never code branches. CI/CD exists as
workflow files that **have never run** (no `workflows` permission): R8 stands until a
green Actions run, and no pipeline claim can be made before that. Secrets are
environment-scoped; the same image is promoted, not rebuilt.

---

## 13. Regulatory architecture (design constraints, not legal advice)

* **Licensing** — a CEMAC *établissement de paiement* requires MINFI agrément after a
  COBAC avis, with 500M XAF paid-up capital. Until then PayDay may operate as a
  technical provider to a licensed institution, **not** as a wallet issuer taking
  customer funds. This constrains everything: custody, float, opening a wallet to the
  public. It must be resolved before real money.
* **AML/CFT** — risk-based CDD, PEP/sanctions screening, 10-year retention, STR to
  ANIF. Architecture must reserve: a screening hook at onboarding and at transfer, an
  immutable audit trail (exists), and a case-management surface (does not).
* **Data protection** — Law 2024/017: prior authorisation, DPO, DPIA, breach
  notification, and **prior approval for cross-border transfers** (the reason object
  storage and backups must be region-pinned).
* **Consumer protection** — published fees, limits and timelines must equal enforced
  ones. This is now both a product rule and an architectural one: the published table
  should be generated from the same configuration the system charges from.

---

## 14. What is needed from outside this repository (urgent)

These block work that cannot be done by writing code. Ordered by how much they unblock.

| # | Needed | From | Blocks |
| --- | --- | --- | --- |
| 1 | **Frontend repo / built app URL + its API base URL** | Frontend engineer | Verifying the client contract (envelope, errors, money, filters, state handling) before it hardens |
| 2 | **A decision on the deposit fee** (keep 0.5% and publish it, or drop it) | Product | LB-14 rows 5; publishing the fee table honestly |
| 3 | **The real daily/monthly/balance ceilings** (500k or 1M? is 5M balance cap right for our licence class?) | Product + compliance | LB-14 rows 6; limit enforcement values |
| 4 | **MTN sandbox credentials + Orange dev credentials + a public HTTPS callback URL** | Partner-portal work | A5 — the only thing that can confirm the operator contracts |
| 5 | **SMS provider decision + sender ID registration** (MTN sender ID takes up to 3 weeks) | Procurement | WS-1, and OTP for password reset (LB-1) |
| 6 | **KYC/IDV vendor decision + storage region** | Compliance | WS-6/LB-2, and mandatory app-store financial declaration |
| 7 | **Licensing path clarity** (own agrément vs partner with a licensed PSP) | Founders | Whether custody is even permitted; shapes the ledger and float design |
| 8 | **Staging target** (cloud account, region, Postgres/Redis) | Founders | M2, all deployment evidence, R8 |

---

## 15. Decisions required (register)

| ID | Decision | Owner | Blocks |
| --- | --- | --- | --- |
| D-26 | Fee rounding mode (currently `HALF_UP`, configurable) | Product/Finance | Comms on fee amounts |
| D-27 | Deposit fee: keep or drop (see #2 above) | Product | Published fee table |
| D-28 | Limit values: daily/monthly/balance ceilings | Product + Compliance | Enforcement defaults |
| D-29 | Send routing: one endpoint (proposed) vs separate internal/external actions | Product | Frontend Send screen shape |
| D-30 | Double-entry journal vs paired single-entry rows | Engineering | Revisit at merchant settlement / agent float |

---

## 16. Build order implied by this design

1. **Internal P2P transfer** — DONE (2026-09-17). `POST /wallet/transfer`, atomic
   two-leg move with a conservation test, instant and free, external recipients
   delegated to the operator payout.
2. **Monthly-limit + max-balance enforcement and KYC gating on money movement** —
   DONE in the same increment (LB-15/16/17).
3. **M2: staging, secrets, observability** — the first environment where any of this
   can be demonstrated to someone else (needs §14 #8).
4. **SMS + notification outbox (WS-1)** — needs §14 #5.
5. **KYC documents + review (WS-6)** — needs §14 #6.
6. **Password reset (WS-4)** — after SMS (OTP delivery) and already gated behind the
   revocation work that shipped.
7. **Settlement-file reconciliation** — the auditor's control.
8. **WS-7 load test** — only after 3, so the numbers mean something.
