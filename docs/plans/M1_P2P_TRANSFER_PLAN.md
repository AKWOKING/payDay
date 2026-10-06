# M1 — P2P Transfer: Implementation Plan

**Date:** 2026-09-17 · **Milestone:** M1 of `docs/MVP_EXECUTION_ROADMAP.md`
**Goal:** the frontend's headline action — "Send money to anyone in Cameroon" —
works end to end. PayDay→PayDay is an instant internal ledger move; anything else
delegates to the operator payout that already exists.
**Status:** plan written before code. T1–T7 implemented; evidence in §7.

---

## 1. Why this is the right next increment

* It is **blocked on nothing external**. No operator credentials, no vendor, no
  licence decision: an internal transfer is two rows and two balances.
* It is the **most-published promise** on the live site ("PayDay-to-PayDay
  transfers settle in about six seconds", "Sending to another PayDay wallet is
  free") and already marked 🛑 in `docs/FRONTEND_INTEGRATION_GUIDE.md` (#19).
* It **exercises the ledger invariants** that everything later depends on —
  conservation, locking order, idempotency, limits — on the cheapest possible
  path, before merchant settlement and agent float arrive.

## 2. Codebase review (verified 2026-09-17)

| Location | Finding | Consequence |
| --- | --- | --- |
| `models/transaction.py:8-16` | `TransactionType` is `DEPOSIT \| WITHDRAW`; `TransactionChannel` is `MTN \| ORANGE \| UBA` | Both need a new label. On PostgreSQL these are **native enum types** (`transactiontype`, `transactionchannel` — verified by compiling the DDL), so the migration must `ALTER TYPE ... ADD VALUE`, which is not doable on SQLite and cannot be rolled back. |
| `models/transaction.py:39-44` | One row per movement, `amount > 0` check constraint, no direction | Two legs of one transfer are indistinguishable, and `amount` cannot carry a sign. Needs `direction`. |
| `services/wallet_engine.py:78-95` | `get_cumulative_daily_volume` sums **WITHDRAW only** | Outgoing volume would ignore transfers. |
| `wallet_engine.py:98-117` | `validate_withdrawal_capacity` checks balance + **daily only** | **`monthly_limit` is never enforced anywhere in the codebase** despite the column, the exception class and the admin endpoint (LB-15). |
| `wallet_engine.py:211-239` `credit_deposit` | No ceiling on any credit | No maximum balance exists, though the site publishes 5,000,000 (LB-16/second half). |
| `transaction_manager.py:254-330` | PIN verification is inline inside `initiate_withdrawal` (throttle, lockout, auto-freeze, counter clear) | Must be extracted, not duplicated — the transfer path needs the same control and a copy would drift. |
| `api/deps.py:56-62` `get_current_verified_user` | **Dead code**; `KycRequiredError` is never raised | No money movement requires KYC today. |
| `notification_service.py:26-49` | Message built from `type` + `status`; else-branch calls a withdrawal "withdrawal" | A transfer would be announced to both parties as a withdrawal. |

## 3. Design decisions

### D-T1. One endpoint, two realities
`POST /wallet/transfer` resolves the recipient's phone number against registered
users. Registered → internal ledger move (instant, free, `channel=PAYDAY`). Not
registered, with a `channel` supplied → delegate to `initiate_withdrawal` and return
that transaction (`type=WITHDRAW`, operator fee applies). Not registered and no
channel → `400 RECIPIENT_NOT_ON_PAYDAY` with the advisory operator suggestion from
`core/msisdn.operator_for`, since Cameroon has number portability and the suggestion
is a hint, not a routing decision.

The client sends one intent; the response says what happened. Duplicating this
routing rule in every client is how contracts drift.

### D-T2. Conservation is structural, not hoped for
Both legs are created in **one DB transaction** with a shared `transfer_group_id`,
and the debit and credit are applied in the same unit of work. A test asserts
`Σ debits = Σ credits` per group across the whole suite, so a future path that
creates only one leg fails loudly rather than balancing by accident.

### D-T3. Deterministic lock ordering
Sender and recipient wallets are locked in **ascending `wallet_id` order**, always,
so two simultaneous A→B and B→A transfers cannot deadlock. On SQLite this is a no-op
(the existing engine already skips `FOR UPDATE` there) — recorded as a residual risk
rather than treated as tested, because no local PostgreSQL exists.

### D-T4. Internal transfers are final and synchronous
No hold: a hold exists to cover the latency of an external partner (`wallet_engine.py:120-148`).
An internal move has no partner, so it is `SUCCESS` with `completed_at` set and a
balance change visible in the same response. This is what makes "about six seconds"
an honest claim — it is actually sub-second.

### D-T5. Fee policy: P2P free, and the minimum fee must not apply
`MIN_FEE_AMOUNT` (25 XAF) currently applies to every type. Applying it to transfers
would make a "free" transfer cost 25 XAF, so the schedule is per-type: percentage
plus an explicit flag for whether the minimum applies. Deposit and withdrawal
behaviour is unchanged (asserted by the existing fee tests).

### D-T6. Earn the declared controls instead of declaring them again
* **Monthly limit** — enforced for outgoing volume (withdraw + transfer), closing LB-15.
* **Maximum balance** — `MAX_WALLET_BALANCE`, enforced on internal credit. Rejected
  **before** the sender is debited, so a full recipient wallet cannot half-complete a
  transfer. Default 5,000,000 to match what the frontend publishes; the value is a
  product/licence decision (D-28), not a number I invented.
* **KYC gating** — outgoing money requires `KycStatus.VERIFIED` (`KYC_REQUIRED`),
  enforced in the service so no future caller can skip it.

### D-T7. Both parties see it, in their own words
Each leg gets its own row, its own notification ("You sent…" / "You received…") and
its own receipt, with the counterparty shown **masked** (`+2376•••233`). The full
number is never returned in a list.

## 4. Task breakdown

| # | Task | Files |
| --- | --- | --- |
| T1 | Migration 005: `direction` (NOT NULL, backfilled by type), `transfer_group_id`, `counterparty_wallet_id`, `counterparty_msisdn_masked`; enum labels via dialect-aware `ALTER TYPE` | `alembic/versions/005_*.py`, `models/transaction.py` |
| T2 | Fee schedule per type (minimum applies only where intended) | `core/config.py`, `services/wallet_engine.py` |
| T3 | Outgoing volume (daily **and** monthly) + credit ceiling + atomic two-wallet move | `services/wallet_engine.py`, `core/exceptions.py` |
| T4 | Extract PIN authorisation; `initiate_transfer` with resolution, self-transfer rejection, delegation | `services/transaction_manager.py` |
| T5 | Request/response schemas: `TransferInitiateRequest`, `direction`, counterparty, group id | `schemas/transaction.py` |
| T6 | `POST /wallet/transfer` | `api/v1/transactions.py` |
| T7 | Direction-aware notifications for both parties | `services/notification_service.py` |
| T8 | Tests + negative controls | `tests/test_sprint7_p2p_transfer.py` |

## 5. What this step will not prove

* Nothing about real operator money: the external branch delegates to a path whose
  operator contracts are still unconfirmed (A5).
* Deadlock safety under PostgreSQL, because no local PostgreSQL exists; the ordering
  is reasoned and asserted by a test that inspects the lock order, not by a race.
* That the ceilings are the *correct* ones commercially or regulatorily (D-28).
* Structuring/velocity detection, sanctions screening and case management — the AML
  track (§14 of the architecture doc), untouched here.

## 6. Risks

| Risk | Mitigation |
| --- | --- |
| R24 — recipient resolution sends money to the wrong wallet | Unique registered phone is the key; both parties see the leg immediately; mismatch is support-visible |
| R25 — enum label addition cannot be rolled back on PostgreSQL | Documented in the migration; downgrade drops the columns and leaves labels in place |
| R26 — ceiling rejects a legitimate inflow at 5,000,000 | Explicit `BALANCE_CEILING_EXCEEDED` error naming the ceiling, and the value is configuration |
| R27 — extracting PIN authorisation changes lockout behaviour | Existing PIN/lockout tests are the regression net; negative control removes the check to confirm the net bites |

## 7. Implementation status

| Task | Status | Evidence |
| --- | --- | --- |
| T1 migration + model | **Done** | `005_transfers_and_direction`: `direction` (NOT NULL, backfilled from `type`), `transfer_group_id` (indexed), `counterparty_wallet_id` (indexed), `counterparty_msisdn_masked`; `TRANSFER`/`PAYDAY` labels added with `ALTER TYPE ... ADD VALUE` inside `autocommit_block()` on PostgreSQL. The migration/model parity test caught a real mismatch while writing it (an index on a column the model did not declare). |
| T2 fee schedule | **Done** | `DEFAULT_TRANSFER_FEE_PERCENTAGE=0`, and the 25 XAF floor now applies to deposits/withdrawals only. Existing fee tests unchanged and passing. |
| T3 limits + atomic move | **Done** | `get_outgoing_volume(window="day"|"month")` sums every DEBIT; `validate_outgoing_capacity` enforces daily **and** monthly; `validate_credit_capacity` enforces `MAX_WALLET_BALANCE`; `lock_wallets_in_order` locks ascending; `transfer_funds` writes both sides in one unit of work. |
| T4 transfer service | **Done** | `_authorize_pin` extracted from the withdrawal path (one copy of the lockout control, not two); `initiate_transfer` resolves, refuses self/inactive, delegates to `initiate_withdrawal` for external recipients, and requires KYC. |
| T5 schemas | **Done** | `TransferInitiateRequest`; `TransactionResponse`/receipt gain `direction`, `transfer_group_id`, `counterparty_*`, `internal`. |
| T6 endpoint | **Done** | `POST /api/v1/wallet/transfer`; OpenAPI baseline regenerated (34 paths, none removed). |
| T7 notifications | **Done** | Direction-aware: "You sent …" / "You received …", counterparty named, neither party told "withdrawal". |
| T8 tests | **Done** | 24 tests in `tests/test_sprint7_p2p_transfer.py`. Full suite **229 passed, 2 skipped** (205 before). |

**Negative controls (each reverted, source restored from a `/tmp` copy):**

* Crediting the sender instead of the recipient — i.e. destroying the money —
  fails `test_transfer_between_two_payday_wallets_is_instant_and_free`. The
  conservation test did **not** catch it (it checked the records, not the
  balances), so it now asserts the balance arithmetic as well.
* Removing the monthly-limit check fails
  `test_monthly_limit_counts_every_kind_of_outgoing_money`.

**Residual, stated rather than hidden:** SQLite serialises, so the deterministic
lock order is reasoned and unit-visible but not race-tested; a PostgreSQL run is
part of M2 (G2). `MAX_WALLET_BALANCE` and the daily/monthly defaults are product
and licence decisions (D-28), not measurements.
