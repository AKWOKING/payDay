# CTO proposal — what I recommend we do about custody, and why

**Date:** 2026-10-04 · **Author:** Lead System Architect / acting CTO
**Question:** given the operator and licensing research, what do I propose?
**Audience:** founders/board (decisions D-31, D-32, D-33 are theirs to make)

---

## 1. The recommendation, in one page

**Adopt the licensed-partner route as our primary path to a custodial wallet, run a legal
business under a licensed umbrella while that matures, and treat our own agrément as a
funded milestone rather than a plan.** Concretely:

| Track | What it is | Timing | Owner |
| --- | --- | --- | --- |
| **A. Interim revenue — operate under someone else's licence** | Agent/technology partner of a licensed institution, and/or sell the platform white-label to a licence-holder | Starts now, revenue inside 90 days | Founders |
| **B. Primary custody route — partner of record** | A bank or payment institution holds the licence and the float; PayDay is the technology and (optionally) the brand. The Wave/CBC structure, COBAC D-2025/122 | Term sheet in 90 days, live in 6–12 months | Founders + counsel |
| **C. Own agrément — only when capital is committed** | 500M XAF paid-up + governance + compliance function | Do not start before the money is in the bank | Founders/board |
| **D. Sandbox — the accelerator worth filing for** | The reform in progress creates a COBAC authorisation for operators that hold no customer funds, plus a 12-month renewable regulatory sandbox with capped volumes | File the moment the text lands | CTO + counsel |

**Why this order.** 500M XAF is not capital we have, so the own-agrément route is not a
plan — it is a fundraising target. The partner route is the only path that preserves the
wallet *and* the P2P feature inside a year, and Wave has already proved COBAC will authorise
it. Track A exists because "wait 9 months for a partner" is not a business: it funds us,
it builds the partner relationships we need anyway, and it proves demand without custody.

**What I am not proposing:** launching real money before the gate clears, building
custodial-only features on the assumption a licence arrives, or spending engineering time on
a licence application. Also not proposing we abandon the wallet — the platform we have built
is the asset that makes the partner conversation credible.

---

## 2. Why not the other two options

**Own agrément now.** Beyond the 500M XAF minimum, a payment institution needs an SA with a
board, COBAC-approved management and statutory auditor, GIMAC interoperability, safeguarding
of float at a CEMAC credit institution, prudential reporting and external audits. Realistic
all-in cost over two years is well above the capital minimum, and the procedural clocks alone
(BEAC up to 3 months, COBAC 3 months) put licensing at 6–12 months *after* the capital is in
place. Starting now without committed capital burns runway on paperwork.

**Pure non-custodial, abandon the wallet.** It is the fastest path to *something* live, but it
either deletes our differentiator (P2P between PayDay users, float, the wallet we built) or —
on the honest reading of Règlement 04/18 art. 5 — replaces the licence we lack with the
*remittance* licence we also lack, because moving money between two third parties is a payment
service regardless of whether we hold a balance in between. It is a fallback, not a strategy.

---

## 3. What the platform already gives us (verified, not aspirational)

This matters for the partner conversations, because it is what we are bringing to them:

| Capability | Evidence |
| --- | --- |
| Ledger with provable conservation — every debit paired with a credit, both legs in one unit of work, asserted by tests | `tests/test_sprint7_p2p_transfer.py`, negative controls on the credit/debit swap |
| Settlement decided by the operator, not by us — callbacks verified, re-queried, idempotent; lost notifications recovered by a sweep | A8 (`M1_A8_CALLBACK_VERIFICATION_PLAN.md`), 25 + 47 tests |
| Reconciliation engine that compares our ledger against partner settlement files and reports mismatches | `services/reconciliation_service.py` |
| Audit trail and operational visibility — stuck-money counts, per-channel failure visibility, sweep health | `api/v1/admin/ops/overview`, LB-19 |
| **Payouts to an arbitrary mobile number already work** | `WithdrawInitiateRequest.destination_phone` is free-form and is passed to the operator payout — see §4 |

A licensed partner's real question is "can you be trusted with our licence?" — and these are
the artefacts that answer it. Most licence-holders in Cameroon do not have this; they have a
licence and a queue of product requests.

---

## 4. The finding that reshapes Track A — and a control we must add

Reading the withdrawal path for this proposal: `destination_phone` is arbitrary user input
(`schemas/transaction.py:48`), it is normalised and prefix-checked, and it is passed straight
to the operator's disbursement call (`adapters/mtn_momo.py:451`, `orange_money.py:411`).
Nothing binds the payout to the user's own verified number.

Two consequences:

1. **Good news for Track A.** "Send money to any number without PayDay holding a balance" is
   *not* a code gap. It is already the behaviour of two endpoints — collection in, payout out.
   What we lack is permission, not plumbing: the disbursement **contract** (Orange's payout
   product is a separate agreement from Web Payment — the trap flagged in the research) and a
   legal umbrella to execute third-party transfers.
2. **A control we owe the regulator and any partner.** A wallet payout to any unverified
   number, with no velocity limit and no destination reputation, is the classic layering
   channel for money laundering — and CEMAC is on the FATF grey list with ANIF requiring
   suspicious-transaction reports within 48 hours. Registered as **LB-22**.

**My recommendation on the control (D-43):** keep arbitrary payouts — they are the product —
but wrap them in: a verified-destination list per user with a step-up (PIN re-entry + cooling
off) for first-time recipients, per-user daily payout velocity limits separate from the
wallet limits, destination-level anomaly detection (many users paying one number), operator
name-match where available, and a documented AML programme the partner can audit. This is
also exactly what a partner-of-record will demand in diligence, so it is Track B work too.

---

## 5. Decisions I need from the board, and when

| # | Decision | Needed by | Consequence of delay |
| --- | --- | --- | --- |
| D-31 | Custody route: partner-of-record primary (my recommendation), with own agrément contingent on committed capital | 2 weeks | Every month without a partner is a month of runway spent on nothing; partner diligence itself takes months |
| D-32 | Operator contracts: direct with MMC and OMCM, and explicitly request the **disbursement** product from both | Now | Orange payout may not be grantable later on the terms we want; MTN float model shapes withdrawal UX |
| D-33 | Payout funding (float) and who holds it under each route | With the term sheet | Uncertain withdrawal experience at launch |
| D-40 | Interim business: agent/technology-partner and/or white-label sale to a licence-holder | 4 weeks | No revenue, no market learning, weaker negotiating position |
| D-41 | Sandbox: file when the reform text lands | On publication | We lose the only mechanism that lets an unfunded startup run custodial features on real users with caps |
| D-43 | Payout controls per §4 | Before real money | Uncontrolled third-party payouts; a partner's compliance team will find it anyway |

**Two non-negotiables in whatever we sign.** (1) **Data:** customer data, transaction data and
documents stay in-region, and we keep the right to use aggregated, de-identified data
(law 2024/017 makes cross-border processing a prior-authorisation matter — a US-hosted BaaS
decides D6 by accident). (2) **Portability:** on termination we retain the customer
relationship and carry a defined export of our ledger and audit trail, so the platform is
not a sunk asset inside someone else's licence.

---

## 6. What engineering does in parallel (no wasted work)

Every item already queued on the critical path is needed under **all** the routes above, which
is why the direction of this decision does not stop the build:

* **LB-3 SMS** — notifications and the OTP that LB-1 depends on; needed by any partner.
* **LB-1 password reset** — account recovery; a partner's risk review asks for it.
* **LB-6 KYC** — identity verification; the product limits are already gated on it, and a
  partner inherits our KYC or substitutes their own.
* **LB-5 load test on PostgreSQL** — capacity evidence for a partner's technical due diligence.
* **§4 payout controls (LB-22)** — AML controls, needed by every route that moves money.
* **Backup/restore rehearsal (C5)** — a partner's first question about an outsourced ledger.

**What we do not build yet:** agent float management, interest, multi-currency, agent
hierarchies — all custodial-only and route-dependent. Building them now is the "declared but
never wired" failure mode this codebase has already paid for twice (LB-15/16, LB-20).

---

## 7. The 90-day plan I would run

**Days 1–14.** Board decides D-31/D-40. Assemble the document pack (RCCM, NIU, patente, RIB in
the company's exact name, statutes, CNI, proof of address, a company MTN SIM, an HTTPS domain)
— days of effort, gates every route. Brief counsel with six specific questions: may we operate
as an agent/distributor of a licensed PSP; what does a partner-of-record filing require of us
and of them; does a technology-services agreement avoid the payment-services definition; how
do the sandbox criteria read; what AML programme must exist at launch; what must the customer
terms and privacy notice say.

**Days 15–45.** Three parallel conversations, all with the same script and the same evidence
pack (platform capability summary, reconciliation and audit exports, security posture):
1. **Konoom** — Cameroon's newest licensed payment institution, licensed July 2026 and
   entering mobile payments. They have the licence and need distribution and product; we have
   product and need a licence. This is the most promising conversation we can have, precisely
   because OMCM and MMC are competitors with their own wallets.
2. **One bank and one EMF** — the Wave/CBC template, and note that **UBA is already a planned
   channel in our architecture**, so the bank conversation has a product answer attached. A
   bank that wants a wallet without building one is the partner profile.
3. **Operators (MTN via the Partner Portal and `MoMoCorporate.CM@mtn.com`; Orange via
   `developer.orange.com` or an Orange Business agency)** — requesting collections *and*
   disbursements explicitly, with the fee, settlement and limit questions answered in writing.

**Days 45–90.** If a partner LOI exists: start integration, filings and the AML programme,
with our team in the technical lead. If not: ship the interim business — white-label/platform
licence to a licence-holder, or a controlled non-custodial product only if counsel confirms
the umbrella covers it — and take 10–50 real users through it to answer D18/D19/D20 with data
rather than guesses.

---

## 8. What would change my mind

* A committed 500M XAF+ raise → own agrément becomes the primary route, because owning the
  float and the customer is worth more than a revenue share, and the filings are faster once
  the capital exists.
* A partner offering terms that capture the customer relationship, data, or portability as
  described in §5 → walk; the Wave route works because the fintech keeps the product and brand.
* The reform text landing with a **first-category authorisation** available to us (payment
  initiation / account information, no customer funds, COBAC authorisation rather than
  agrément) → that becomes the primary route immediately: it is the cheapest legal way to run
  the transfer product we have already built, and it fits the platform as it stands.

---

## 9. Sources

1. `docs/research/API_ACCESS_MTN_OM_CAMEROON.md` — operator access paths, KYC packs, costs,
   the Orange payout trap, the licence gate, MINFI/COBAC enforcement, Wave and Konoom
   precedents, the reform's first-category and sandbox provisions.
2. Code as it stands: `services/transaction_manager.py`, `adapters/mtn_momo.py`,
   `adapters/orange_money.py`, `services/reconciliation_service.py`,
   `api/v1/admin/ops/overview`, `schemas/transaction.py`.
3. `docs/design/SYSTEM_ARCHITECTURE.md` — D-26…D-39, external needs, build order.
4. `docs/MVP_EXECUTION_ROADMAP.md` — C5, launch-blocker sequencing, D18–D20.
