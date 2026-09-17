# Frontend review — what the live site tells the backend

**Date:** 2026-09-17 · **Reviewed:** `https://pay-day-iota.vercel.app/` (frontend
engineer's deployment) · **Backend state reviewed at:** commit `8970259`
(M1 A1–A8) · **Test baseline re-verified while writing this:** 205 passed, 2 skipped.

**Purpose:** the frontend is being built against a published product promise. This
review inventories that promise, checks each item against what the backend
actually does, and separates three very different kinds of gap: things we must
stop claiming, numbers we must reconcile, and features we must build.

---

## 1. What was reviewed, and what could not be

**Reviewed (server-side fetch, 2026-09-17):** `/`, `/about`, `/faq`, `/contact`.

**Could not be reviewed, and why this matters:**

* **No client→API integration is observable.** Nothing on these pages reveals an
  API base URL, request shape or response handling. `/login`, `/dashboard` and
  `/sitemap.xml` all failed to render for the fetcher — and so did a deliberately
  nonsense route (`/zzz-no-such-route-xyz`), which means the failure is the
  fetcher refusing non-prerendered routes, **not** evidence that those routes are
  missing. I therefore cannot say whether an app surface is deployed, nor what it
  calls. **If a frontend repository or an app URL exists, reviewing its API client
  would be far more actionable than reviewing marketing copy.**
* Everything below about the backend was verified by reading this repository; the
  virtualenv had to be rebuilt first (`.venv` is excluded from workspace
  snapshots), and the 205-test baseline reproduces, so the backend facts are
  current.

The repo's own contract for the frontend is `docs/FRONTEND_INTEGRATION_GUIDE.md`
(33 published paths in `docs/api/openapi-baseline.json`). That guide is
conservative — it marks ten of nineteen screens as blocked or needing adjustment.
The live site goes **considerably further** than the guide: it sells features the
guide already knows do not exist.

---

## 2. Claim-by-claim: published promise vs backend reality

Legend: 🔴 material and must change now · ⚠️ needs a decision/alignment ·
🟠 feature not implemented (product scope) · ✅ matches the backend.

| # | Published on the site | Backend reality (evidence) | |
|---|---|---|---|
| 1 | "Licensed and supervised under CEMAC regulations"; "2023 First licence — PayDay is approved as a payment service provider" (`/about`) | No licence, no application in progress in this workspace. CEMAC/Cobac: a payment institution needs MINFI agrément after a COBAC avis and **500M XAF paid-up capital** (`docs/LAUNCH_BLOCKER_ROADMAP.md`, licensing research). | 🔴 |
| 2 | "10k+ Trusted by thousands across Cameroon"; "more than 10,000 people … across 1,200 agent locations" (`/`, `/about`) | No production users, no agent model anywhere in the code, no agent-network feature at all. | 🔴 |
| 3 | "Funds held with partner banks, never lent out"; "audited annually" (`/about`, `/faq`) | No trust/cantonment account, no audit, and no code that holds customer funds separately. | 🔴 |
| 4 | "Dial **#237#** from any line linked to your account to freeze the wallet" (`/faq`) | No USSD gateway, no freeze-by-code path. The real controls are an admin wallet-status endpoint and PIN lockout. A fraud victim following this instruction gets **no freeze** — this is the most dangerous item on the site. | 🔴 |
| 5 | Fees: P2P free · wallet→MTN/Orange **1.0%** · agent 1.5% · bills FCFA 100 (`/faq`), plus "without hidden fees" / "no hidden charges" (`/`) | Withdrawal → **1.0% matches** (`core/config.py:81`). But **a deposit is charged 0.5% (min 25 XAF)** (`config.py:80,82`) and the deposit path credits `amount − fee` (`transaction_manager.initiate_deposit`; tests assert 10 000 → fee 50 → net 9 950). The deposit fee is charged but **not published**, while the site promises no hidden charges. | ⚠️ |
| 6 | "A verified personal wallet can send up to FCFA **1,000,000 per day** and hold up to **5,000,000**" (`/faq`) | Enforced default is **500 000/day** and 5 000 000/month (`config.py:44-45`), checked in `wallet_engine` (`daily_volume + amount > wallet.daily_limit`). There is **no maximum-balance rule anywhere** in the code. | ⚠️ |
| 7 | "PayDay-to-PayDay transfers settle in about six seconds"; "Sending to another PayDay wallet is free"; "Send money to anyone in Cameroon" (`/faq`, `/`) | **There is no wallet-to-wallet transfer endpoint.** `TransactionType` is only `DEPOSIT \| WITHDRAW`; no P2P service, model or route exists. Our own guide already marks screen 19 (Dashboard "Send") 🛑 blocked. | 🟠 |
| 8 | Bills: "ENEO electricity, Camwater, Canal+ subscriptions, airtime and data bundles"; "post to the provider the same day" (`/faq`) | Nothing: no biller model, no airtime, no provider integration, no same-day job. | 🟠 |
| 9 | "Withdraw at any agent location"; agent withdrawal 1.5% (`/faq`) | Nothing: withdrawal is a mobile-money disbursement (MTN transfer / Orange payout) only. | 🟠 |
| 10 | Merchant accounts: "payment link, a QR code for the counter, and a REST API with test keys. Settlement runs daily to your wallet or bank account" (`/faq`); "Developer API access" (`/contact`) | No merchant accounts, no API-key issuance, no QR/link, no daily settlement job. A public API exists (33 paths) but there is no self-serve onboarding for it. Bank settlement is UBA, which is "Phase 2" and raises `CHANNEL_NOT_AVAILABLE`. | 🟠 |
| 11 | "link your bank account … A bank account is optional and only needed for large settlements" (`/`, `/faq`) | UBA adapter is not implemented — `adapters/factory.py` returns a Phase 2 `PayDayException`. | 🟠 |
| 12 | "Pending transfers clear automatically within 15 minutes"; "normally complete within two minutes, or fifteen during peak hours" (`/faq`, `/contact`) | Not guaranteed by design: MTN waits for the customer's PIN and can sit `PENDING`/`EXPIRED`; Orange documents `INITIATED`/`PENDING` until the customer acts. A8's sweep re-queries every 120 s but **only settles when the operator has a verdict** — if the operator has none, the transaction stays `PROCESSING`. | ⚠️ |
| 13 | "Verification usually completes in under five minutes" (`/faq`) | `POST /kyc/submit` takes an ID **number and type only** (JSON, no document upload) and review is a manual admin action (`api/v1/kyc.py:13-34,51`). No automated verification, no document storage — that is LB-2 / WS-6, unbuilt. Screens 17/18 are 🛑 in our own guide. | 🟠 |
| 14 | "Support in English and French, seven days a week" (`/about`) | No locale handling in the API (no `Accept-Language`, no i18n layer) and notification templates are English-only. | ⚠️ |
| 15 | "device binding, and a fraud team that reviews unusual activity" (`/about`) | No device binding, no fraud-review queue (`grep` for device/locale returns nothing). Admin tooling covers KYC review, audit logs, wallet freeze. | 🟠 |
| 16 | "Android 8 and above, iOS 14 and above"; "the web wallet works in any modern browser and supports the same transfers" (`/faq`); "Download App" (`/`) | No mobile app and no web wallet in this repository; the "Download App" link points back at `/` (no store listing). | 🟠 |
| 17 | "no dormancy fees, no monthly charges and no minimum balance" (`/faq`) | Matches: no such fees exist in the ledger. | ✅ |
| 18 | "you see the fee before you confirm, every time" (`/about`) | The capability exists — `POST /api/v1/public/fee-calculator` and `fee`/`net_amount` on every transaction response. Only the *published table* has to stop disagreeing with it (row 5). | ✅ |

---

## 3. What this means for backend development

The site is a useful requirements document — but it currently reads as a
description of a licensed, five-year-old business with ten thousand customers.
Priorities, in the order they should be done:

**P0 — stop the false claims (today, no code, highest value).** Rows 1–4 and the
traction/history narrative in `/about` (a fabricated 2022–2024 timeline) should
come down or be marked as vision/coming soon. Claiming a CEMAC licence and an
agent network you do not have is regulatory exposure in a supervised sector, and
"dial #237# to freeze" actively harms a fraud victim. This is cheap to fix and
nothing in the backend blocks it.

**P1 — reconcile published numbers with enforced ones (config + product decision).**
Decide: (a) does a deposit really cost 0.5%, and if so it must be published as
prominently as the 1.0% withdrawal fee; (b) is the daily limit 500 000 or
1 000 000; (c) do we want the 5M maximum-balance rule the FAQ promises (it does
not exist). Then make the published table derive from the same source the system
charges from, so drift fails a test instead of misleading a customer.

**P2 — P2P transfer (row 7).** The headline promise and the site's own
differentiator, already 🛑 in `docs/FRONTEND_INTEGRATION_GUIDE.md` §4.2/§19.
Wallet→wallet is ledger-internal, so it needs no operator: it is the cheapest
*sellable* feature on this list and it unblocks the frontend's main action.

**P3 — honest SLAs (rows 12, 13).** The frontend needs a status vocabulary it can
display truthfully. Recommendation: have the transaction response carry an
explicit `expected_resolution`/`next_check` hint from the sweep configuration
rather than the app inventing "15 minutes" — and delete the fixed-time promises
until a load test and real operator timings exist (WS-7/LB-5 is still pending, so
there is no measured basis for any of them).

**P4 — the sold-but-absent tracks (rows 8–11, 13–16).** Bills, agents, merchant
accounts, bank linking, mobile app, EN/FR, device binding. Each is a product
track, not a sprint item. They should not appear as available until scheduled.

**P5 — what this review could not do.** I could not see a single API call the
frontend makes. If a frontend repo or app URL can be shared, the highest-value
next action is reviewing its API client against the guide's envelope, error-code
and money-format rules — that is where real contract bugs will be, and it is not
observable from marketing pages.

---

## 4. New blocker registered

**LB-14 — the public site publishes licences, traction and capabilities the
platform does not have.** Severity 🔴. Evidence is the table in §2: a claimed
CEMAC licence, a fabricated 2022–2024 history, 10 000 customers, 1 200 agent
locations, a USSD freeze code, bills/airtime, merchant accounts with API keys, and
fees and limits that do not match what the system charges and enforces. This is
recorded in `docs/LAUNCH_BLOCKER_ROADMAP.md`; rows 1–4 (licence, traction,
cantonment, USSD) and rows 5–6 (fees, limits) are the material ones.

---

## 5. Positive findings worth keeping

* The fee-preview promise ("you see the fee before you confirm") is already
  supported by the API — the frontend can honour it today.
* "No dormancy fees, no monthly charges, no minimum balance" is true of the
  ledger as built.
* The site's information architecture (wallet, deposits, withdrawals, history,
  receipts, KYC, notifications, EN/FR) matches the shape of the API that exists,
  so most of the marketing surface can become true incrementally rather than
  needing a redesign.
* The `/about` positioning — "one wallet that speaks to MTN MoMo and Orange
  Money" — is exactly what M1+A8 built, once A5 confirms it against the
  operators.
