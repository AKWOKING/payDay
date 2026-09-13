# M1 — A8: Operator Callback Verification and Authoritative Settlement

**Date:** 2026-09-13 · **Milestone:** M1 of `docs/MVP_EXECUTION_ROADMAP.md`
**Goal:** a real MTN or Orange callback can settle a transaction — proven authentic,
with the ledger moved only on the operator's *own* answer — and `TELCO_MODE=live`
becomes startable.
**Status:** implemented. B1–B6 done, tested and pushed; B7 (the periodic sweep)
was pulled **into** this step after the research showed both operators document
missed notifications — see §8 for what landed and what is still unproven.

---

## 1. Research inputs (read 2026-09-13; sources in §7)

Facts that changed the design, as opposed to confirming it:

1. **MTN's callback body is not `{status}`.** It is
   `{"externalId", "amount", "currency", "financialTransactionId", "transactionStatus", "payee"}`.
   The status key is `transactionStatus`. `externalId` is *our* value — the adapter
   already sends `externalId = transaction_id`, so it is a reliable match key.
2. **MTN does not sign callbacks.** There is no signature, HMAC or shared secret on
   the callback path. MTN's own integrator guidance is explicit: treat the callback
   as a trigger and **re-query the status endpoint** before trusting it. The
   callback host must match the `providerCallbackHost` registered on the API user,
   and MTN retries non-2xx responses, so the endpoint must be idempotent and answer 200.
3. **`GET /collection/v1_0/requesttopay/{X-Reference-Id}` is the authority**, with
   statuses `PENDING`, `SUCCESSFUL`, `FAILED`, `EXPIRED`. A `404` means the reference
   is not visible *yet* as well as "never existed" — so it must not be read as failure.
   `X-Reference-Id` is what this codebase stores as `Transaction.external_ref`.
4. **Orange's callback body is three fields:** `{"status", "notif_token", "txnid"}`.
   No `order_id`, no amount, no reference. The *only* way to know which order it
   belongs to is to have stored `notif_token` from the initiation response and match
   on it. Orange vendors state this explicitly; we currently store neither it nor
   `order_id`.
5. **Orange's initiation response also carries `notif_token`** alongside `pay_token`
   and `payment_url`. `pay_token` is what this codebase stores as `external_ref`;
   `order_id` and `notif_token` are dropped on the floor today.
6. **Orange's status API is `POST {base}/transactionstatus`** with
   `{order_id, amount, pay_token}`, returning `{"status", "order_id", "txnid"}`.
   Statuses: `INITIATED`, `PENDING`, `EXPIRED`, `SUCCESS`, `FAILED`. It takes all
   three inputs, so `order_id` must be persisted at initiation. A second, older
   API generation uses `GET .../paymentstatus/{payToken}` — the suffix is already
   configurable (`ORANGE_STATUS_PATH`) and stays so until A5 confirms which one the
   contracted merchant API uses.
7. **Both vendors document missed and late notifications** and advise a periodic
   status sweep as the safety net. This codebase has no scheduler, so that cannot be
   built in this step without inventing infrastructure — see B7.
8. **Both vendors advise verifying by status API even after receiving a callback**:
   "Before updating the payment in your system, retrieve the payment status and
   compare the status received with that sent to your endpoint."

## 2. Codebase review — what exists, with line numbers

| Location | What it does today | Consequence for A8 |
|---|---|---|
| `schemas/transaction.py:90` `WebhookCallbackPayload` | `external_ref` (required), `status` (required), optional `transaction_id`, `amount`, `currency`, `reason`, `metadata` | **Not an operator shape.** A real MTN callback has no `external_ref` field (it has `externalId`) and no `status` (it has `transactionStatus`); a real Orange callback has neither, and only three fields. A real callback is rejected with 422 and the transaction never settles. Registered as **LB-12**. |
| `api/v1/webhooks.py:22,62` | Validates the invented shape, calls `verify_webhook_signature`, then `process_webhook` | Rejects live (interlock) so the 422 is unreachable today; must be rebuilt around operator-native parsing. |
| `services/transaction_manager.py:431` `process_webhook` | Looks up by `transaction_id` or `external_ref`, then maps `payload.status` straight to SUCCESS and credits the wallet | **Trusts the request body to move money.** With verification removed or bypassed, a single unauthenticated POST mints balance. Registered as **LB-13**. |
| `services/transaction_manager.py:159,192` | `external_ref = channel_res.channel_ref`; no provider-side identifiers persisted | MTN: `channel_ref = reference_id` (the `X-Reference-Id`) — enough to requery. Orange: `channel_ref = pay_token`; `order_id` and `notif_token` are lost, so neither the callback can be matched nor the status endpoint called. |
| `adapters/mtn_momo.py:463` `query_status` | `GET {base}/collection/v1_0/requesttopay/{ref}` (or `disbursement/v1_0/transfer/{ref}`), maps via `map_status` | The requery primitive A8 needs already exists and is correctly addressed. Non-200 is flattened to `FAILED`, which would convert a pending 404 into a failure — must be distinguished. |
| `adapters/orange_money.py:427` `query_status` | `GET {base}/{status_path}/{channel_ref}` | Wrong verb/shape for the documented `POST /transactionstatus` contract; needs to take `order_id` + `amount` + `pay_token`. |
| `models/transaction.py:44` | `extra_data` JSON exists; no provider columns | Nothing to look up an Orange callback by. |
| `alembic/versions/` 001–003 | Latest is `003_add_token_version` | A8 adds `004`; the roadmap's planned 004/005 (WS-1, WS-6) renumber to 005/006 — they do not exist yet, so this costs nothing but must be recorded (R21). |
| `tests/` (20 call sites) | POST the invented shape to `/api/v1/webhooks/{mtn,orange}` and rely on settlement | The invented shape must keep working **in mock mode** or the suite and the mock-telco dev tool break. |

## 3. Design decisions

### D-B1. Two shapes, chosen by mode — never both in live
`WebhookCallbackPayload` stays as the **internal normalised shape**, used by the
mock simulator and by `mock-telco` dev tooling. Each adapter gains a pure
`parse_callback(body) -> ProviderCallback | None` that understands its own vendor's
body. The route accepts the operator shape in `sandbox`/`live`, and the normalised
shape **only** in `mock`. Reason: the normalised shape carries a status and an
identifier with no authenticity evidence at all; accepting it while real money moves
is a mint, not a convenience.

### D-B2. The callback is a hint; the requery is the truth
No status from a callback body ever moves the ledger. The route parses the callback,
identifies the transaction, calls the operator's status API, and settles on **that**
answer. If the requery does not return a conclusive status, the transaction stays
`PROCESSING`, the endpoint returns `202`, and the event is logged for reconciliation.
The endpoint never answers "settled" when it has not settled. Rationale: both
vendors recommend exactly this, MTN signs nothing, and Orange signs nothing.

### D-B3. Authenticity, per operator
- **MTN** — no signature exists, so authenticity is *derived*: the requery is made
  with credentials only we hold, for a reference we generated, matched to a
  transaction we initiated and that is still `PROCESSING`. A forged callback can at
  most cause a requery, which returns the true status. Note honestly: MTN's
  `providerCallbackHost` matching is *their* control; we do not depend on it.
- **Orange** — the callback's `notif_token` is compared against the token stored for
  that order, in constant time (`hmac.compare_digest`). A missing or mismatched token
  is rejected `403` before any lookup. This is the control Orange documents.
  The requery then runs as the second, independent check.

### D-B4. Persist what verification needs (migration 004)
Three nullable columns on `transactions`: `provider_order_id`, `provider_notif_token`
(indexed — it is the Orange callback lookup key), `provider_txn_id` (the operator's
own transaction id: MTN `financialTransactionId`, Orange `txnid`, for support and
reconciliation). No backfill is possible or needed: existing rows predate live mode.

### D-B5. Amount cross-check, and refusing to settle on a mismatch
If a requery reports an amount different from the transaction's, the transaction is
**not** settled: it is flagged for manual review and the endpoint answers `202`.
Settling a 200 XAF payment against a 20 000 XAF deposit is worse than leaving it
open. Where the operator's status response carries no amount (Orange's documented
response does not), this check cannot run — recorded as a residual risk, not
hidden behind a passing test.

### D-B6. Idempotency and replay
The existing early return on a final state stays. Duplicate callbacks answer `200`
with the same body, because MTN retries non-2xx. Callbacks for a transaction that is
not `PROCESSING` are acknowledged without effect.

### D-B7. Scope boundary: the missed-callback sweep is A9, not A8
Both vendors document notifications that never arrive. Closing that needs a periodic
job; there is no scheduler in this codebase (`services/task_queue.py` is an in-process
queue with no loop, and reconciliation is admin-triggered). Choosing and building that
infrastructure is its own step — **A9** — and A8 ships the requery primitive it needs.
Until A9 exists, a transaction whose callback never arrives stays `PROCESSING` until an
operator looks, which is the safe direction: no money is credited without the
operator's own answer.

### D-B8. The interlock is removed only when it is genuinely satisfied
`validate_telco_configuration` currently refuses `live` outright. A8 replaces that
refusal with the real preconditions (credentials, public HTTPS callback base,
callback verification present) and a test asserting live is now *startable* — but the
money path still cannot be called live until B5's requery tests pass.

## 4. Task breakdown

| # | Task | Files |
|---|---|---|
| B1 | Migration 004 + model columns + parity test | `alembic/versions/004_*.py`, `models/transaction.py` |
| B2 | `ProviderCallback` type + pure `parse_callback` for both operators, with golden bodies from §1 | `adapters/base.py`, `adapters/mtn_momo.py`, `adapters/orange_money.py` |
| B3 | Persist provider refs at initiation (MTN reference id, Orange order_id/notif_token) | `services/transaction_manager.py`, both adapters' `initiate_*` |
| B4 | `query_status` contracts: MTN distinguishes 404/network from FAILED; Orange uses the documented `{order_id, amount, pay_token}` shape | both adapters |
| B5 | Verified settlement path: requery → amount check → settle; `process_webhook` no longer trusts a body status; route rebuilt with mode dispatch; 202 vs 200 semantics | `api/v1/webhooks.py`, `services/transaction_manager.py` |
| B6 | Tests: golden callback parses, forged/replayed callbacks, requery-failure leaves PROCESSING, amount mismatch refuses, duplicate is idempotent, legacy shape rejected outside mock, live now startable | `tests/test_sprint7_*` |
| B7 | *Deferred:* periodic requery sweep for stale `PROCESSING` (D-B7) | — |

## 5. What this step will *not* prove

- That MTN or Orange actually call these URLs with these bodies. **Only A5 can**, and
  A5 needs credentials for both operators.
- That the Orange merchant API generation in use is the `POST /transactionstatus`
  one; `ORANGE_STATUS_PATH` stays configurable until A5.
- That a notification which never arrives gets settled — that is A9.
- That MTN's `providerCallbackHost` registration is correct for our domain — an
  operator-side configuration, verifiable only in A5.

## 6. Risks introduced or retired

| Risk | Status after A8 |
|---|---|
| R21 — migration numbering collides with WS-1/WS-6 | Introduced, mitigated by recording the renumbering (004 → 005 → 006) in the roadmap |
| R22 — a forged callback triggers requeries (resource use, not money) | Accepted: rate limiting already applies to the route; no ledger effect is possible |
| R23 — Orange status response carries no amount, so D-B5 cannot check it | Accepted and visible; recorded for A5 |
| LB-12 — real callbacks cannot be parsed (422) | Retired by B2/B5, pending A5 confirmation |
| LB-13 — unauthenticated body can credit a wallet | Retired by B5: the ledger moves only on a requery answer |

## 7. Sources (read 2026-09-13)

1. MTN callback body fields incl. `transactionStatus`, `financialTransactionId`, idempotency by `externalId`, retry-on-non-2xx, host validation — https://lobehub.com/skills/africandigitalassetframework-africa-stack-skills-mtn-momo
2. "Validate callbacks via API: never trust callback data alone" — https://dev.to/lepresk/how-to-integrate-mtn-mobile-money-in-php-complete-guide-j81
3. MTN status endpoint, 404 meaning, status vocabulary, `X-Target-Environment` requirements — https://momodevelopercommunity.mtn.com/momo-api-production-q-a-7/problem-in-request-to-pay-status-233
4. MTN status semantics (PENDING / SUCCESSFUL / FAILED / EXPIRED; failure reasons) — https://momodevelopercommunity.mtn.com/product-updates/momo-api-error-response-enrichment-186
5. MTN request-to-pay + status reference, 202/404/409 handling, polling guidance — https://medium.com/@bmskmike/mtn-mobile-money-momo-request-to-pay-api-complete-technical-reference-for-nigerian-developers-4c148732dceb
6. Sandbox callback behaviour and "production allows HTTPS only" — https://gist.github.com/chaiwa-berian/5294fdf1360247cf4561c95c8fa740d4
7. Orange notification body `{status, notif_token, txnid}` and "store notif_token to compare" — https://www.y-note.cm/comment-deployer-lapi-orange-money/
8. Orange initiation response `{pay_token, payment_url, notif_token}` — https://github.com/Foris-master/orange-money-sdk
9. Orange `POST /transactionstatus` contract `{order_id, amount, pay_token}` → `{status, order_id, txnid}`, status vocabulary — https://github.com/pathus90/om4j
10. Vendor guidance to re-check status after a notification, and to poll every ~2 min because notifications are missed — https://www.npmjs.com/package/@spreeloop/orange_money

## 8. Implementation status

| Task | Status | Evidence |
|---|---|---|
| B1 — migration 004 + model columns | **Done** | `alembic/versions/004_provider_callback_refs.py` adds `provider_order_id`, `provider_notif_token` (indexed), `provider_txn_id` (indexed) to `transactions`, matching `models/transaction.py`. Migration/model parity tests: 5 passed. WS-1/WS-6 renumber to 005/006 (R21). |
| B2 — `ProviderCallback` + pure parsers | **Done** | `adapters/base.py` defines `ProviderCallback`; `mtn_momo.parse_callback` and `orange_money.parse_callback` are pure and tested against the exact documented bodies. Each refuses the other operator's body, and a body with no match key returns `None` rather than being guessed at. |
| B3 — persist provider references | **Done** | Both initiation paths copy `provider_order_id`/`provider_notif_token`/`provider_txn_id` from `ChannelResponse`; Orange fills `order_id` + `notif_token` from its initiation response (previously discarded, which made an Orange callback unmatchable). |
| B4 — status-requery contracts | **Done** | MTN: `GET .../requesttopay/{X-Reference-Id}`, with **404 mapped to PROCESSING, not FAILED** — MTN returns 404 for references that are not readable yet, and failing there would fail approved payments. Orange: documented `POST {status_path}` with `{order_id, amount, pay_token}`. |
| B5 — verified settlement + route rebuild | **Done** | `transaction_manager._apply_settlement` is now the single ledger-mutation path; `settle_from_provider` returns `SETTLED`/`ALREADY_FINAL`/`INCONCLUSIVE`/`AMOUNT_MISMATCH`. The route dispatches on `TELCO_MODE`: operator-native + requery outside mock, normalised shape in mock only. 200 / 202 / 503 / 403 / 400 semantics documented in the module docstring. |
| B6 — tests | **Done** | `tests/test_sprint7_callback_verification.py`: 25 tests — golden bodies for both operators, cross-operator refusal, constant-time token comparison, forged-status rejection, amount mismatch, duplicate idempotency, cross-channel rejection, invented-shape refusal outside mock, and five sweep tests. Negative control: making the ledger follow the callback body again fails the forgery test (`assert 200 == 503`). |
| B7 — periodic sweep | **Done (scope change)** | `services/status_sweep.py`: bounded passes (`TELCO_STATUS_SWEEP_BATCH_SIZE`), minimum age, mock channels skipped, per-transaction error isolation, result counted in a `SweepReport`. Started from the app lifespan when `TELCO_STATUS_SWEEP_ENABLED` is set; **required** for `TELCO_MODE=live` (the deployment-level preconditions in §3/D-B8 became a config check). |
| Live mode | **Now startable** | The blanket refusal is gone. `TELCO_MODE=live` requires credentials, a public HTTPS `PUBLIC_BASE_URL`, and the sweep. Pinned by `test_sprint7_callback_verification.py::test_live_mode_requires_a_running_status_sweep` and `test_sprint7_money_path.py::test_live_mode_is_startable_now_that_callbacks_are_verified`. |

**Regression status:** full suite **205 passed, 2 skipped** (180 before this step).

**Still unproven — no claim is made:** that MTN and Orange actually send these
bodies to these URLs, that the contracted Orange API generation is the
`POST /transactionstatus` one, and that our `providerCallbackHost` registration
is correct. All three are A5, and A5 needs credentials.

**Residual risks now live:** orange status responses carry no amount, so the
amount cross-check (D-B5) cannot run for that channel (R23); a forged callback
can still provoke a requery, though not a settlement (R22).
