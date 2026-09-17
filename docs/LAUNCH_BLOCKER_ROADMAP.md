# PayDay — Launch-Blocker Roadmap

**Version:** 1.1
**Date:** 2026-09-07
**Owner:** Backend
**Status:** In progress — **WS-0 core, WS-3 (except owner notification) and
WS-5 are implemented and tested** (2026-09-07); the rest remains proposed but
**still awaits the product decisions in §2**, especially D1 (SMS), D6/D7 (KYC),
D21 (Redis) and D18 (pilot volume).

---

## 0. Implementation status (2026-09-13)

Updated after the WS-0 / WS-3 / WS-5 build and the WS-2 build. See
`docs/reports/SPRINT_5_REDIS_AND_THROTTLING.md` and
`docs/reports/SPRINT_6_TOKEN_REVOCATION.md` for the work records.

| Workstream | Status | Remaining before "done" |
| --- | --- | --- |
| WS-0 | 🟢 Core done | CI `redis:7` service job written in `ci.yml` but **unverified** — workflows cannot be pushed/run until the GitHub `workflows` permission is restored (R8) |
| WS-3 (LB-6) | 🟢 Implemented | Deliberately **not** done: deliverable 3 (notify the account owner on threshold breach) — needs WS-1/notification delivery (D1) |
| WS-5 (LB-4) | 🟢 Implemented | Confirm D-extra (24h TTL default is in place); production Redis (D21) |
| WS-2 (LB-7) | 🟢 Implemented | Deliverable 6 (per-`jti` denylist for single-device logout) deliberately **not** built — logout is account-wide; no PIN-reset call site exists yet (see the sprint report) |
| WS-1/4/6/7 | 🔴 Not started | WS-4 additionally depends on WS-2, which is now done. WS-1/6/7 gated on D1/D6-D12/D18 per §5 |

**New on 2026-09-13:** LB-8…LB-11 (money-path reconnaissance, §1) and the
sequenced execution plan for everything remaining — see
`docs/MVP_EXECUTION_ROADMAP.md`. That document is now the canonical order of
work; this one remains the defect register and decision list.

Test baseline: **229 passed, 2 skipped** (2026-09-17, after M1 A1–A8; was 133/2
before that work, and 100/1 before WS-0/3/5 + WS-2). One skip is the real-Redis
integration test that runs when `PAYDAY_TEST_REDIS_URL` is set, as CI does; the
other is the `alg=none` test the local JOSE library refuses to mint.

---

## 0. How to read this

This plans the work that must land before PayDay can hold real customer money.
It is not a feature roadmap: the six designed features in
`FRONTEND_INTEGRATION_GUIDE.md` §4 (P2P send, bill payments, notification
categories, referral codes, PIN login, recipient names) are **deliberately out
of scope here**. They are product scope. This document covers only defects and
absences that make the current build unsafe to launch.

Every claim about current behaviour cites `file:line` and was read from source,
not assumed. Where I am proposing rather than reporting, the text says
"proposed".

**Estimates** are engineering-days for one backend engineer who knows this
codebase, including tests and review, excluding procurement lead time and
excluding the product decisions in §2. They are estimates, not commitments.

| Legend | Meaning |
| --- | --- |
| 🔴 | Launch-blocking — real money must not move until this is done |
| 🟠 | Required for the pilot to be judged GREEN rather than AMBER |
| 🔒 | Blocked on a product decision (§2) |
| 💰 | Has a recurring cost that product must approve |
| ⏳ | Has external procurement/contract lead time |

---

## 1. The blocker set

The original list was five. Writing this plan surfaced two more, both in the
authentication layer, both found by reading the code rather than the docs.

| ID | Blocker | Sev | Source of truth |
| --- | --- | --- | --- |
| LB-1 | No password reset exists — permanent lockout | 🔴 | `api/v1/auth.py` has 5 routes; none reset a credential |
| LB-2 | No KYC document upload — identity unverifiable | 🔴🔒 | `api/v1/kyc.py` — no `UploadFile` anywhere |
| LB-3 | Notifications are never delivered | 🔴🔒💰⏳ | `notification_service.py:68` fabricates a device token; no outbound call |
| LB-4 | PIN counter is per-process — 5N attempts on N replicas | 🟠 | `transaction_manager.py:49` `_failed_pin_attempts: Dict[str, int] = {}` |
| LB-5 | No load test against PostgreSQL | 🟠🔒 | Sprint 4 numbers are in-process ASGI over SQLite |
| **LB-6** | **Login is completely unthrottled** | 🔴 | `auth_service.py:92-96` — no counter, no lockout, no rate limit |
| **LB-7** | **Refresh tokens cannot be revoked** | ✅ | Fixed 2026-09-13 — `users.token_version` + `tv` claim; validity is no longer status-only |

### LB-6 — newly found

`AuthService.login_user` compares the password and raises on mismatch. That is
all it does. There is no attempt counter, no lockout, no backoff, and
`grep -rniE "ratelimit|rate_limit|slowapi|limiter" src/` returns **nothing** —
there is no rate limiting anywhere in the application.

The 5-attempt wallet freeze people point to lives in
`transaction_manager.py:246` and guards the **transaction PIN only**. The
password login path has no equivalent. An attacker can guess passwords against
`POST /auth/login` at whatever rate the network allows, and the account owner
gets no signal, because the freeze that would alert them is on a different
credential.

This is worse than LB-4. LB-4 raises an attacker's PIN budget from 5 to 5N;
LB-6 makes the password budget unbounded today, on one replica.

### LB-8 … LB-11 — found 2026-09-13 (money path reconnaissance)

Registered while planning the MVP sequence in `docs/MVP_EXECUTION_ROADMAP.md`,
which is now the canonical record with the evidence, the reproduced arithmetic
and the fix plan. Summary, because these belong in the blocker list:

| ID | Finding | Severity |
| --- | --- | --- |
| **LB-8** | **The platform cannot move real money.** Both adapters are module-level singletons built with `use_mock=True` (`adapters/mtn_momo.py:290`, `adapters/orange_money.py:283`) and `core/config.py` has no telco credentials, base URLs or environment switch at all. The live code paths are unreachable in production; every test passes because the mock branch returns before the payload is built. | 🔴 |
| **LB-9** | **Three representations of the same amount.** XAF is ISO 4217 exponent 0 (no centimes). MTN is sent `str(amount)` (`"1000.00"`), Orange is sent `int(amount)` — which **truncates** `1000.99` to `1000` — while the ledger quantises to 0.01 and accepts sub-franc requests (`schemas/transaction.py:11`). Silent, one-directional reconciliation drift. | 🔴 |
| **LB-10** | **MSISDN format wrong for at least one operator.** `_clean_msisdn` only strips `+`, sending `237699123456` to both; Orange Money documents the 9-digit local form for `subscriber_msisdn`. No operator/prefix validation either. | 🟠 |
| **LB-11** | **No test asserts an outbound telco payload** (`grep requesttopay\|webpayment\|subscriber_msisdn tests/` → nothing), which is precisely why LB-8/9/10 survived 133 green tests. | 🟠 |

The landing page served by `main.py` advertises "MTN MoMo — Adapter Active" and
"Orange Money — Adapter Active". Until LB-8 is fixed that is a demo claim.

**Status of LB-8…LB-11 (2026-09-13):** fixed in `86539c5` — per-mode telco
configuration with fail-closed validation, one money authority
(`core/money.py`), per-operator MSISDN formatting, and 47 tests pinning the
exact operator payloads (negative control: reintroducing the truncation fails
four golden tests). Not closed: the endpoints, headers and Orange's ambiguous
`amount` type are pinned by tests, **not yet confirmed by the operators** — that
is task A5, which needs real sandbox credentials.

---

### LB-12 … LB-13 — found 2026-09-13 (callback-path reconnaissance)

Found while planning A8. These are the two defects that made a real callback
either impossible or dangerous, and both are fixed by the same work.

| ID | Finding | Severity |
| --- | --- | --- |
| **LB-12** | **The operators' callbacks could not be parsed.** `schemas/transaction.py:90` `WebhookCallbackPayload` is a PayDay-invented shape (`external_ref` and `status` required). A real MTN callback sends `externalId` and `transactionStatus`; a real Orange notification sends exactly `{"status", "notif_token", "txnid"}` — no order id, no reference, no amount. Every genuine callback would have failed validation with 422 and **no transaction would ever have settled**. | 🔴 |
| **LB-13** | **An unauthenticated request body could credit a wallet.** `transaction_manager.process_webhook` read `payload.status` and credited the ledger from it. Neither operator signs callbacks (MTN signs nothing; Orange echoes a per-order token), so with notifications enabled this was a money-printing endpoint: anyone able to POST JSON could settle a deposit. The only thing preventing exploitation was the A8 interlock refusing to start in live mode. | 🔴 |

**Fixed (A8):** operator-native parsing per adapter, channel-scoped lookup (MTN
by `externalId`, Orange by the `notif_token` stored at initiation), constant-time
`notif_token` comparison for Orange, an authoritative status requery that decides
the outcome, an amount cross-check that refuses to settle a mismatch, and a
periodic sweep (`services/status_sweep.py`) for notifications that never arrive.
25 tests in `tests/test_sprint7_callback_verification.py`; negative control:
making the ledger follow the callback body again fails the forgery test
(200 where 503 is required). Not closed until A5 confirms the operators send
these shapes.

---

### LB-14 — found 2026-09-17 (frontend review)

> **Open.** Full evidence and per-claim mapping:
> `docs/reports/FRONTEND_REVIEW_2026-09-17.md`.

The frontend engineer's deployed site (`pay-day-iota.vercel.app`) publishes a
product, a history and a regulatory status that the platform does not have. This
is a defect against the project's own rule — *document only what genuinely exists
and verifiably passes* — and it is the most externally visible thing about PayDay
right now.

| Published claim | Reality |
| --- | --- |
| "Licensed and supervised under CEMAC regulations"; "2023 First licence — approved as a payment service provider" | **No licence.** A CEMAC *établissement de paiement* needs MINFI agrément after a COBAC avis, with 500M XAF paid-up capital. None of that has happened. |
| "10k+ Trusted by thousands"; "10,000 people … across 1,200 agent locations"; a 2022–2024 company timeline | Fabricated. No production users, no agent model in the codebase, no such history. |
| "Funds held with partner banks, never lent out"; "audited annually" | No trust/cantonment account, no audit, and no code holding customer funds separately. |
| "Dial **#237#** from any line linked to your account to freeze the wallet" | **No USSD gateway exists.** A fraud victim following this gets no freeze. The real controls are an admin freeze endpoint and PIN lockout. |
| Fee table omits deposits; site promises "no hidden charges" | The backend **charges 0.5% (min 25 XAF) on deposits** (`config.py:80,82`) and credits `amount − fee`. Published table lists only the 1.0% withdrawal fee. |
| "send up to FCFA 1,000,000 per day and hold up to 5,000,000" | Enforced default is **500,000/day** (`config.py:44`); **no maximum-balance rule exists** at all. |
| P2P transfers, bills (ENEO/Camwater/Canal+/airtime), agent cash-out, merchant accounts with API keys, bank linking, mobile app, EN/FR support, device binding | None implemented. Some are already 🛑 in `docs/FRONTEND_INTEGRATION_GUIDE.md`. |

**Why it is a launch blocker rather than a marketing note:** rows 1–4 are
regulatory and consumer-protection exposure in a supervised sector, and the
`#237#` instruction can directly harm a user. The fee and limit rows are the
published-vs-enforced class of defect this register already tracks for other
surfaces (LB-9 was the same class, one layer down).

**Cheapest correct action:** take down or re-label the licence, traction,
cantonment and USSD claims today; reconcile the fee/limit table with
configuration; move the genuinely-planned features behind a "coming soon"
label. A decision is needed on whether the deposit fee stays (then it must be
published) or goes.

---

### LB-15 … LB-17 — found 2026-09-17 (P2P build)

Found while implementing internal transfers; all three are "declared but never
wired", which is the failure mode this register exists to catch.

| ID | Finding | Severity |
| --- | --- | --- |
| **LB-15** | **`wallet.monthly_limit` was never enforced.** The column existed, admins could set it, the API returned it, and `MonthlyLimitExceededError` was defined — but nothing ever raised it. Daily limits were checked for withdrawals only, so any other debit path had no ceiling at all. | 🔴 |
| **LB-16** | **KYC was never required to move money.** `KycRequiredError` and `get_current_verified_user` existed and were used by nothing: an unverified user could deposit, withdraw and (once it existed) send. | 🔴 |
| **LB-17** | **No ceiling on any credit.** No credit path checked a maximum balance, although the published site promises 5,000,000 XAF for a verified wallet. | 🟠 |

**Fixed (2026-09-17, `POST /wallet/transfer` increment):** limits are enforced on
every DEBIT path (daily and monthly), every credit passes a ceiling check before
the counterparty is debited, and outgoing money requires a verified identity
while receiving and self-funding do not. Tests:
`tests/test_sprint7_p2p_transfer.py` (24). The published figures and the enforced
ones now agree by default, which was one half of LB-14's fee/limit mismatch — the
deposit-fee half still needs a product decision (D-27).

---

### LB-7 — newly found

> **Fixed 2026-09-13 (WS-2).** Tokens now carry a `tv` claim compared against
> `users.token_version`, which `revoke_all_sessions()` increments; logout,
> suspension/closure and (when it ships) password reset therefore evict. The
> text below is the original finding, kept as the record of what was wrong and
> why the ordering mattered. Per-device revocation remains unimplemented.
> See `docs/reports/SPRINT_6_TOKEN_REVOCATION.md`.

`refresh_tokens` decodes the JWT, looks the user up, and issues a new pair if
the user is `ACTIVE`. There is no denylist, no `jti` tracking, no token
version. Consequences:

- A stolen refresh token is valid for its full 7 days and **cannot be revoked**
  short of suspending the whole account.
- Logout is client-side only. The server has no concept of it.
- **This makes a correct LB-1 impossible.** The entire point of a password
  reset is to evict whoever compromised the account. If reset cannot invalidate
  outstanding refresh tokens, the attacker keeps access for up to 7 days *after*
  the victim resets. Building LB-1 without LB-7 produces a reset flow that
  provides false assurance — arguably worse than having none, because support
  will tell users they are safe.

LB-7 is therefore a hard prerequisite of LB-1, not a nice-to-have.

---

## 2. Product decisions required before code

Grouped by what they block. Nothing in the 🔒 rows can be built correctly
without an answer; guessing here produces rework or a compliance problem.

### 2.1 Blocking LB-3 (notification delivery)

| # | Decision | Why it can't be a backend call |
| --- | --- | --- |
| D1 | **Which SMS aggregator**, and is the contract signed? | ⏳ Procurement lead time in Cameroon is typically weeks. Options include the MTN/Orange bulk-SMS programmes, Africa's Talking, Twilio, or a local aggregator. Deliverability and per-message price differ sharply by route. |
| D2 | **Which events send an SMS** vs push/in-app only | 💰 Today *every* transaction writes both an SMS and a push row. At real volume SMS is a per-message cost. Someone who owns the P&L must decide whether a failed 500 XAF deposit is worth an SMS. |
| D3 | **Sender ID / short code** registration | ⏳ Regulator- and operator-registered; also has lead time. |
| D4 | Is push in scope for launch, or SMS-only? | Determines whether we build device-token registration now (LB-3b) or defer it. |
| D5 | Monthly SMS budget ceiling and what happens at the cap | Fail closed (block transactions) or fail open (transact silently)? This is a risk-appetite question. |

### 2.2 Blocking LB-2 (KYC upload)

| # | Decision | Why |
| --- | --- | --- |
| D6 | **Where do ID images live**, and in which jurisdiction? | Cameroon data-residency expectations for financial PII may forbid a US/EU bucket. Options: in-country VPS disk, an in-region S3-compatible provider, or a vendor. This decides the whole storage design. |
| D7 | **Manual review or a verification vendor?** | Manual means building an admin review UI and staffing it. A vendor (Smile ID, Dojah, IDfy) means an integration, a contract, and per-check cost. Completely different builds. |
| D8 | **Retention period** for ID images, and deletion policy | Drives whether we need a scheduled purge job and a legal-hold flag. |
| D9 | Is a **selfie / liveness** check required, or documents only? | The design draws a selfie step. Liveness is a vendor capability, not something we build. |
| D10 | **Who may view a document**, and is viewing audited to a named officer? | Compliance requirement; drives RBAC granularity beyond today's ADMIN role. |
| D11 | Registration currently **requires** `id_document_no` before any document exists (§4.5 conflict). Should registration stop requiring it? | Changes the registration contract, which the mobile team is building against. |
| D12 | Is document **virus scanning** required? | Adds a dependency (ClamAV or vendor) and latency. |

### 2.3 Blocking LB-1 (password reset)

| # | Decision | Why |
| --- | --- | --- |
| D13 | **Delivery channel for the reset OTP** — SMS, email, or both? | 💰 SMS costs money and depends on D1. Email is free but `email` is **nullable** on the user model, so it cannot be the only channel. |
| D14 | **Post-reset fraud hold.** Standard anti-account-takeover practice is to block withdrawals for 24h after a password reset. Do we? | Genuine tension: it protects victims but frustrates legitimate users. Risk owner must choose. |
| D15 | Does a password reset also **clear the transaction PIN**? | If yes, an attacker who resets the password still can't move money without re-setting a PIN — but a legitimate user is forced through PIN setup again. |
| D16 | OTP length and TTL (proposed: 6 digits, 10 minutes) | Fine to accept the proposal; recording it as a decision. |
| D17 | Reset attempts before the account locks, and lock duration | Risk appetite. |

### 2.4 Blocking LB-5 (load test)

| # | Decision | Why |
| --- | --- | --- |
| D18 | **Expected pilot volume**: registered users, transactions/day, peak-hour multiple | Without this, a load test measures an arbitrary number and proves nothing. This is the single most important input. |
| D19 | **Latency SLO** the business will commit to | Sprint 4 used p95 ≤ 60 ms as a self-set placeholder. Product should own the real target. |
| D20 | **Acceptable downtime** / maintenance window | Determines whether migrations can take a lock and whether we need zero-downtime deploys. |

### 2.5 Cross-cutting

| # | Decision | Why |
| --- | --- | --- |
| D21 | **Is Redis approved as production infrastructure?** | LB-4, LB-6, LB-1 all need shared state. Without Redis the honest answer is a permanent single-replica ceiling. |
| D22 | Who is the **on-call owner** at launch, and where do alerts go? | There is currently no alerting of any kind. |

---

## 3. Architecture: what these blockers share

Sequencing matters more than effort here, because the blockers are not
independent. Three shared pieces of infrastructure sit underneath them:

```
                    ┌──────────────────────────┐
                    │  WS-0  Redis + limiter   │  ← D21
                    └────┬─────────┬───────┬───┘
                         │         │       │
        ┌────────────────┘         │       └──────────────┐
        │                          │                      │
┌───────▼────────┐        ┌────────▼────────┐    ┌────────▼────────┐
│ LB-6  login    │        │ LB-4  PIN       │    │  OTP storage    │
│ throttling     │        │ counter → Redis │    │  (needed by     │
└────────────────┘        └─────────────────┘    │   LB-1)         │
                                                  └────────┬────────┘
┌──────────────────────────┐                               │
│ WS-1  LB-3 SMS delivery  │  ← D1,D2,D3 ⏳                │
└────────────┬─────────────┘                               │
             │                                             │
             └──────────────┬──────────────────────────────┘
                            │
                   ┌────────▼─────────┐      ┌──────────────────┐
                   │ LB-7 token       │      │ LB-2  KYC upload │
                   │ revocation       │      │ (independent)    │
                   └────────┬─────────┘      │   ← D6..D12 ⏳   │
                            │                └──────────────────┘
                   ┌────────▼─────────┐
                   │ LB-1  password   │
                   │ reset            │
                   └────────┬─────────┘
                            │
                   ┌────────▼─────────┐
                   │ LB-5  load test  │  (last — tests the finished system)
                   └──────────────────┘
```

**The critical path is `WS-0 → LB-3 → LB-7 → LB-1 → LB-5`.**
(WS-0 and LB-7 are done as of 2026-09-13; the remaining chain is LB-3 → LB-1 →
LB-5.)

Two non-obvious dependencies drive that:

1. **Password reset cannot ship before SMS delivery works.** A reset OTP has to
   reach the user. Today nothing is delivered (`notification_service.py` writes
   rows and marks them `SENT` with no outbound call), so LB-1 built today would
   have no way to reach anyone. LB-3 is a prerequisite of LB-1, not a parallel
   track.
2. **Password reset is not secure before LB-7.** Covered above — LB-7 is now
   done, so this dependency is discharged for whoever picks up WS-4.

**LB-2 (KYC) is fully independent** and is the natural parallel track if a
second engineer is available.

---

## 4. Workstreams

### WS-0 — Shared infrastructure: Redis and rate limiting

**Blocks:** LB-1, LB-4, LB-6 · **Est:** 3–4 days · **Decision:** D21
**Status 2026-09-07:** implemented (core), see §0 status table.

Today the only shared mutable state is a process-local dict. Everything that
needs a counter — PIN attempts, login attempts, OTP attempts, OTP storage —
needs somewhere real to live.

**Deliverables**

1. `redis>=5.0` in `pyproject.toml`; `redis.asyncio` client.
2. `src/payday/core/redis_client.py` — pooled client, `get_redis()` FastAPI
   dependency, health probe wired into `/public/health`.
3. Config: `REDIS_URL`, `REDIS_REQUIRED: bool`.
4. **Fail-closed rule (important).** If `ENVIRONMENT == "production"` and Redis
   is unreachable, the app must **refuse to start**. A silent fallback to an
   in-memory dict would quietly reintroduce LB-4 and LB-6 in production, which
   is precisely the class of bug this project already shipped once with the
   migration drift. Dev/test may use an in-memory shim, chosen explicitly by
   config, never as a fallback.
5. `src/payday/core/ratelimit.py` — a small fixed-window limiter over Redis
   (`INCR` + `EXPIRE`, atomic via a Lua script or pipeline). Reusable
   dependency: `RateLimit(key_fn, limit, window)`.
6. `docker-compose.yml`: add the `redis` service. The current file deliberately
   omits it with a comment saying nothing talks to a broker — that comment gets
   deleted here.
7. CI: `redis:7` service container in `ci.yml`.

**Tests**
- Limiter allows N, rejects N+1, recovers after the window.
- Two independent limiter instances sharing one Redis enforce a *shared* budget
  (this is the test that would have caught LB-4).
- Production + unreachable Redis ⇒ startup raises.

**Definition of done:** a counter incremented by one process is visible to
another; `/public/health` reports Redis; CI green with the service container.

---

### WS-1 — LB-3: make notifications actually deliver

**Est:** 4–6 days after D1 ⏳ · **Decisions:** D1–D5

**Current behaviour, precisely.** `NotificationService` builds message strings
and writes `Notification` rows. Every row is created with
`status=NotificationStatus.SENT` and `sent_at=now`. There is no HTTP client, no
provider SDK, no queue hand-off. PUSH rows use
`recipient=f"device-token-{user.user_id[:8]}"` — a synthetic string, not a real
token, at `notification_service.py:68`, `:101`, `:140`.

So `SENT` currently means "a row was inserted". Nothing has ever been delivered
to a customer.

**Deliverables**

1. **Provider abstraction.** `src/payday/services/notifications/` with a
   `NotificationTransport` protocol (`send(recipient, message) -> DeliveryResult`)
   and adapters: `LoggingTransport` (dev/test), `SmsTransport` (per D1),
   optionally `FcmTransport` (per D4). Chosen by config — same pattern as the
   existing telco adapters, so it stays consistent with the codebase.
2. **Honest status lifecycle.** Extend `NotificationStatus` to
   `PENDING → SENDING → SENT → DELIVERED | FAILED`. `SENT` means the provider
   accepted it; `DELIVERED` means the provider confirmed handset receipt (via
   DLR callback, if D1's provider supports it).
3. **Migration 003:** add `provider_message_id`, `attempt_count`, `last_error`,
   `failed_at` to `notifications`; extend the status enum. Must be written
   dialect-aware and idempotent, following `002_fix_schema_drift`.
4. **Retry with backoff.** Dispatch through the task queue; retry transient
   failures 3× with exponential backoff; mark `FAILED` and alert after
   exhaustion. Note the queue is in-process (`task_queue.py`) — see Risk R3.
5. **Device tokens (only if D4 says push is in scope).** New table
   `device_tokens(user_id, token, platform, is_active, last_seen_at)`,
   `POST /notifications/devices`, `DELETE /notifications/devices/{id}`. Replace
   the fabricated recipient with a real lookup; if a user has no token, do not
   create a PUSH row at all.
6. **Provider DLR webhook** (if supported) reusing the existing HMAC + anti-replay
   webhook machinery.
7. **Cost control per D2/D5:** a config-driven map of event → channels, so
   turning off SMS for low-value events is a config change, not a deploy.

**Tests**
- Transport failure ⇒ row is `FAILED` with `last_error`, never `SENT`.
- Retry succeeds on the second attempt ⇒ `SENT`, `attempt_count == 2`.
- No device token ⇒ no PUSH row (asserts the fabricated-token bug cannot return).
- DLR callback transitions `SENT → DELIVERED`.
- Existing notification assertions updated — several currently assert `SENT`
  and will need to mean something different.

**Definition of done:** a real SMS arrives on a real Cameroonian handset in
staging, and its `Notification` row reflects the provider's actual response.

---

### WS-2 — LB-7: revocable sessions

**Blocks:** LB-1 · **Est:** 2–3 days
**Status 2026-09-13:** implemented. Deliverables 1–5 are in; deliverable 6
(per-`jti` denylist) was deliberately not built — logout is account-wide, and
that limitation is documented rather than glossed. The migration is revision
**003**, not the 004 planned below: the plan assumed WS-1 (notification
delivery) would land first, but WS-2 was executed first and the chain head was
still `002_fix_schema_drift`. The "PIN reset" call site is not wired yet —
`POST /auth/set-pin` is a first-time *set* for new accounts, so revoking there
would sign a user out mid-onboarding; the PIN-reset call site belongs to the
LB-1 flow (D15). See `docs/reports/SPRINT_6_TOKEN_REVOCATION.md`.

**Deliverables**

1. **Migration 004:** `users.token_version INTEGER NOT NULL DEFAULT 0`.
2. Include `tv` (token version) in access and refresh JWT claims
   (`core/security.py`).
3. `get_current_user` and `refresh_tokens` reject a token whose `tv` ≠ the
   user's current `token_version`.
4. `AuthService.revoke_all_sessions(user_id)` — increments `token_version`.
   Called on password reset, PIN reset, admin suspension, and user-initiated
   logout.
5. `POST /auth/logout` — a real server-side logout.
6. Optional (proposed): per-`jti` denylist in Redis for immediate single-device
   revocation. `token_version` alone is coarse — it kills every session at once.
   For launch, coarse is acceptable and much simpler.

**Tests**
- Refresh token issued before a revocation is rejected after it.
- Access token issued before a revocation is rejected after it.
- Logout invalidates the refresh token.
- Admin suspension immediately invalidates active sessions.

---

### WS-3 — LB-6: throttle authentication

**Est:** 2 days (after WS-0)
**Status 2026-09-07:** implemented except deliverable 3 (owner notification —
blocked on WS-1/D1).

**Deliverables**

1. Rate-limit `POST /auth/login` on **two** keys: per-phone-number and
   per-source-IP. Per-IP alone is defeated by a botnet; per-account alone lets
   one IP spray many accounts. Proposed: 5 failures / 15 min per account,
   20 / 15 min per IP.
2. Progressive response: after the threshold, return `429` with `Retry-After`
   and error code `RATE_LIMITED` (new — add to `core/exceptions.py`, which
   currently defines 14 codes and has none for throttling).
3. **Notify the account owner** on threshold breach, reusing WS-1. A brute-force
   attempt the victim never hears about is a silent failure.
4. Apply the same limiter to `POST /auth/register` (spam), `POST /auth/refresh`,
   and every LB-1 reset endpoint.
5. Constant-time behaviour: `login_user` currently returns quickly when the user
   does not exist and slowly (bcrypt) when it does — a **user-enumeration
   oracle**. Always run a bcrypt comparison against a dummy hash on the
   not-found path.
6. Trust `X-Forwarded-For` **only** from a configured proxy allowlist, or the
   IP limit is trivially spoofed.

**Tests**
- 6th failed login in the window ⇒ 429 with `Retry-After`.
- Successful login resets the counter.
- Limit is shared across two app instances via Redis.
- Timing: not-found and wrong-password paths are within the same order of
  magnitude.
- Spoofed `X-Forwarded-For` from a non-proxy source does not reset the bucket.

---

### WS-4 — LB-1: password reset

**Est:** 4–5 days · **Depends on:** WS-0, WS-1, WS-2, WS-3 · **Decisions:** D13–D17

**Proposed flow** — three legs, so the OTP is never the thing that changes the
password:

```
POST /auth/password-reset/request   { phone_number }
  → always 202, identical body and timing whether or not the user exists
  → generates 6-digit OTP, stores bcrypt(OTP) in Redis under
    pwreset:{user_id}, TTL 600s, attempt counter 0
  → dispatches via WS-1

POST /auth/password-reset/verify    { phone_number, otp }
  → max 5 attempts, then the OTP is burned
  → returns a single-use reset token (JWT, 15 min, aud="pwreset", jti in Redis)

POST /auth/password-reset/confirm   { reset_token, new_password }
  → validates jti is unused, burns it
  → sets the new hash, calls revoke_all_sessions()  ← WS-2
  → per D14: optional 24h withdrawal hold
  → per D15: optional PIN clear
  → notifies the user on ALL channels that the password changed
```

**Why three legs.** A two-leg flow (`otp + new_password` in one call) means the
OTP must stay valid for as long as the user takes to type a password, and it
conflates "prove identity" with "change credential". Splitting them lets the OTP
die immediately on use.

**Non-negotiables**

- **No user enumeration.** `/request` returns the same status, body, and
  approximate timing for unknown numbers. Rate-limited regardless, or the
  endpoint itself becomes an enumeration oracle by 429 timing.
- OTP stored **hashed**, never plaintext, never logged. Existing security tests
  already assert credential shapes never appear in responses — extend them.
- The reset token is `aud`-scoped so it cannot be used as an access token.
- Reuse of a burned token returns the same generic error as an invalid one.

**Also in scope: PIN reset.** `SetPinRequest` already requires the current
password (`schemas/auth.py:45`), so a user who knows their password can already
change their PIN. Once password reset exists, PIN recovery follows from it, and
FIG §4.6 is satisfied. Note `ChangePasswordRequest` at `schemas/auth.py:50` is
defined but unused — either wire it to an authenticated change-password route
here or delete it.

**Tests**
- Full happy path end-to-end.
- Unknown phone ⇒ 202, no row, no message.
- Expired OTP, wrong OTP ×5, reused reset token ⇒ all rejected.
- **After reset, a refresh token issued before the reset is rejected** (the
  LB-7 integration test — this is the one that matters).
- OTP never appears in any response body or log line.
- `/request` is rate-limited per phone and per IP.

---

### WS-5 — LB-4: move the PIN counter to Redis

**Est:** 1–2 days (after WS-0) · **Small, but closes an AMBER condition**
**Status 2026-09-07:** implemented — 24h TTL default is in
(`PIN_FAILURE_TTL_SECONDS`); confirm the D-extra recommendation with product.

Replace `TransactionManager._failed_pin_attempts` (`transaction_manager.py:49`)
with an atomic Redis counter: `INCR pin:fail:{user_id}` + `EXPIRE`. Clear on
success (`:279`); freeze the wallet at 5 (`:251`).

Open question for product (D-extra): the counter currently only clears on
success or freeze — it never expires. With Redis it gains a TTL, which is a
behaviour change (a sliding window forgives old failures). Recommend a 24h TTL
so a user who fails twice today isn't three failures from a freeze forever.

**Tests**
- Two `TransactionManager` instances sharing Redis freeze at a **combined** 5
  attempts, not 5 each. This is the test that proves the multi-replica ceiling
  is lifted.
- Redis unavailable in production ⇒ PIN verification fails closed (refuse the
  transaction), never open.

**Definition of done:** the single-replica caveat is removed from
`FINAL_PROJECT_HANDOVER.md` §9.2 — with the test to justify it.

---

### WS-6 — LB-2: KYC document upload (parallel track)

**Est:** 5–7 days after D6–D12 ⏳ · **Largest single item**

`python-multipart>=0.0.9` is **already** a declared dependency
(`pyproject.toml:25`), so the plumbing is available; only the endpoint is
missing.

**Deliverables**

1. **Migration 005:** `kyc_documents(document_id, user_id, doc_type, storage_key,
   content_type, size_bytes, sha256, uploaded_at, reviewed_by, review_status)`.
   `doc_type ∈ {ID_FRONT, ID_BACK, SELFIE}` per D9.
2. `POST /kyc/documents` — multipart, one file per call.
   - **Validate by magic bytes, not extension or client `Content-Type`.** A
     client-supplied MIME is attacker-controlled.
   - Max 5 MB (config).
   - Allow JPEG/PNG/PDF per D-storage.
   - **Re-encode images and strip EXIF.** Phone photos carry GPS coordinates;
     storing a customer's home location alongside their ID is a privacy
     liability nobody asked for.
   - Compute and store SHA-256 (dedupe + tamper evidence).
3. **Storage per D6.** Abstract behind a `DocumentStore` protocol
   (`LocalEncryptedStore`, `S3CompatibleStore`) so D6 can change late without a
   rewrite. Encrypt at rest — `core/encryption.py` already provides AES-256-GCM
   and is already used for `id_document_no_encrypted`, so reuse it.
4. `GET /kyc/documents` — the user's own metadata (never a public URL).
5. `GET /admin/kyc/{user_id}/documents/{document_id}` — streamed, RBAC-gated per
   D10, **every access written to `audit_logs`**. Never a public bucket URL.
6. Extend `GET /kyc/status` with per-document status so Verify Account 1/2 can
   show real progress.
7. Retention/purge job per D8.
8. Registration change per D11.
9. Virus scanning per D12.

**Tests**
- Upload/fetch round-trip; bytes match.
- A `.jpg` whose magic bytes are an ELF binary is rejected.
- Oversize rejected with a clean 413/422, not a crash.
- EXIF GPS stripped from the stored artefact.
- User A cannot read user B's document (IDOR).
- Unauthenticated read rejected; every admin read appears in `audit_logs`.
- Ciphertext at rest does not contain the plaintext bytes.

**Definition of done:** a compliance officer can open a submitted ID in the
back-office and approve or reject against the image.

---

### WS-7 — LB-5: load test against PostgreSQL

**Est:** 3–5 days · **Depends on:** everything above + a deployed staging env
**Decisions:** D18–D20

Must be **last**. A load test of a system that is about to gain Redis calls, SMS
dispatch, and file uploads measures a system that will not exist.

**Prerequisites**
- Staging deployed — which needs the CI/CD pipelines pushed, which needs the
  **GitHub `workflows` permission** (still outstanding; commit `020e9f2` is
  local-only and no pipeline has ever run).
- PostgreSQL 15 sized like production, with the production pooling
  configuration.
- Seed data at realistic scale per D18 — a table with 200 transactions tells us
  nothing about index behaviour at 2 million.

**Deliverables**
1. **Out-of-process** driver (k6 or Locust). The Sprint 4 harness runs in-process
   over ASGI and measures application cost only — it cannot see connection-pool
   contention, network, or serialisation, which is exactly where a real system
   falls over. Keep the in-process suite as a fast regression gate; it is not
   the capacity answer.
2. Scenarios weighted per D18: balance reads, transaction history at depth,
   deposit/withdraw under concurrency, webhook bursts.
3. Soak test (≥1h) for connection and memory leaks.
4. Report actual p50/p95/p99 and throughput vs the D19 SLO, plus the breaking
   point and the resource that breaks first.
5. Update `SPRINT_4_REPORT.md` §4 — clearly superseding the SQLite figures
   rather than quietly replacing them.

**Definition of done:** a measured, documented capacity ceiling and a pilot
verdict that can move from AMBER to GREEN on evidence.

---

## 5. Sequencing

Assumes one backend engineer. `‖` marks the parallel track if a second is
available.

| Phase | Work | Days | Gate to start |
| --- | --- | --- | --- |
| P0 | WS-0 Redis + limiter | 3–4 | D21 |
| P1 | WS-3 LB-6 login throttling | 2 | P0 |
| P1 | WS-5 LB-4 PIN counter | 1–2 | P0 |
| P2 | WS-1 LB-3 SMS delivery | 4–6 | **D1 contract signed** ⏳ |
| P3 | WS-2 LB-7 token revocation | 2–3 | — (can overlap P2) · **done 2026-09-13** |
| P4 | WS-4 LB-1 password reset | 4–5 | P2 + P3 |
| ‖ | WS-6 LB-2 KYC upload | 5–7 | D6–D12 ⏳ |
| P5 | WS-7 LB-5 load test | 3–5 | all + staging deployed |

**Single engineer, serial:** ≈ 24–34 engineering days ≈ **5–7 calendar weeks**,
*excluding* procurement.
**Two engineers** (KYC in parallel): ≈ **4–5 calendar weeks**.

**Procurement is the real schedule risk, not engineering.** D1 (SMS contract)
and D6/D7 (storage/vendor) have lead times measured in weeks and gate P2 and the
parallel track respectively. Starting those conversations today costs nothing
and may save a month. If D1 lands late, the critical path
`WS-0 → LB-3 → LB-7 → LB-1` stalls at P2 and password reset slips with it.

**Suggested immediate order regardless of decisions:** WS-0, then WS-3 and WS-5.
None of them need product input beyond D21, they close the worst live
vulnerability (unbounded password guessing), and they build the foundation the
rest sits on.

---

## 6. Risks

| ID | Risk | Impact | Mitigation |
| --- | --- | --- | --- |
| R1 | SMS contract (D1) slips | LB-1 cannot ship; critical path stalls | Start procurement now; build against `LoggingTransport` so only the adapter is late |
| R2 | Redis rejected (D21) | LB-4, LB-6, LB-1 all degrade | Then formally accept a permanent single-replica ceiling and document it as a launch condition — do not pretend otherwise |
| R3 | `task_queue.py` is in-process | Retries die with the process; notifications lost on restart | Persist queue state to the DB, or adopt Celery/ARQ properly. Decide during WS-1 |
| R4 | Migrations 003–005 repeat the `001` drift class | Production `IntegrityError` | The parity test and CI `migrations-postgres` gate from Sprint 4 already cover this — keep them green |
| R5 | WS-1 changes `SENT` semantics | Existing tests assert the old meaning | Update assertions deliberately; do not weaken them to pass |
| R6 | Data residency (D6) decided late | KYC storage rebuilt | The `DocumentStore` abstraction confines the blast radius to one adapter |
| R7 | Load test (D18) run against unrealistic volume | False confidence — the exact failure mode of the Sprint 4 SQLite numbers | Refuse to publish a capacity claim without the D18 inputs |
| R8 | `workflows` permission stays blocked | No staging ⇒ no load test ⇒ pilot stays AMBER | Needs a repo admin to reconnect GitHub with the `workflows` scope |

---

## 7. Definition of done for "launch-ready"

- [ ] LB-1 password reset live, and **provably evicts existing sessions**
- [ ] LB-2 a compliance officer can review a real ID image
- [ ] LB-3 a real SMS reaches a real handset, status reflects the provider
- [ ] LB-4 PIN lockout enforced across replicas, proven by test
- [ ] LB-5 capacity measured on PostgreSQL against an agreed SLO
- [ ] LB-6 login throttled per account and per IP; owner notified
- [x] LB-7 sessions revocable; logout is real — `token_version` + `POST /auth/logout`,
      12 tests incl. re-activation and cross-account isolation (2026-09-13).
      Per-device revocation is **not** implemented: logout ends every session.
- [ ] CI/CD pipelines have **actually run green** at least once
- [ ] Production secrets rotated off the `.env.example` lineage
- [ ] On-call owner named and alerting wired (D22)

Only then is the AMBER verdict in `SPRINT_4_REPORT.md` worth revisiting.

---

## 8. Explicitly out of scope

Tracked in `FRONTEND_INTEGRATION_GUIDE.md` §4; not launch blockers:

§4.1 PIN login · §4.2 P2P send, bill payments, UBA channel · §4.3 notification
categories and read state · §4.4 referral codes · §4.7 recipient-name
resolution · §4.10 avatars, QR, tiers.

Note the overlap: WS-1 touches the notifications table and WS-4 touches auth. If
product wants §4.3 or §4.1, doing them *inside* those workstreams is far cheaper
than a second pass. Worth asking before P2 starts.
