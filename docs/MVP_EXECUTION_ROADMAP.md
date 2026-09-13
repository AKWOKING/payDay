# PayDay — MVP Execution Roadmap

**Version:** 1.0 · **Date:** 2026-09-13
**Scope:** everything left to build, ordered so that a coherent, demonstrable,
defensible MVP exists at **every** point along the way — never a half-built flow.
**Companion docs:** `LAUNCH_BLOCKER_ROADMAP.md` (defect register + decisions),
`FINAL_PROJECT_HANDOVER.md` (architecture), `FRONTEND_INTEGRATION_GUIDE.md`
(client contract), `docs/reports/SPRINT_5_*` and `SPRINT_6_*` (work records).

---

## 1. How to read this, and what is verified

Three kinds of statement appear below, and they are kept visibly separate:

| Kind | Marking | Meaning |
| --- | --- | --- |
| **Verified** | `file:line` + a command or test I ran | Read from this repository, or executed here. Trustworthy. |
| **Sourced** | `[n]` → §10 | Read from an external page on 2026-09-13. Directionally right; confirm before signing anything. |
| **Estimated** | "≈ N days" | Engineering judgement, one backend engineer who knows this codebase, including tests. Not a commitment. |

Nothing in §3 is inferred: every defect claim cites the source line, and the
money-format claims were reproduced in Python before being written down.

**What is verified to work today** (so this document does not read as if the
project is empty): ledger with pessimistic locking, deposits/withdrawals
(end-to-end over an in-process transport), idempotency, reversal, reconciliation,
RBAC back-office, audit log, PII encryption, KYC *submission* (number only),
notification *rows*, contract baseline, 133 passing tests, rate limiting and
cross-replica PIN lockout and revocable sessions (WS-0/3/5/2).

**What has never happened:** a real franc has never moved, a real SMS has never
been sent, an ID image has never been stored, and no pipeline has ever run.

---

## 2. The MVP ladder — what "working" means at each rung

The ordering rule: **no milestone may leave a user-visible flow half-built.** If
the Flutter team is drawing a "Forgot password" button, the endpoint behind it
ships in the same milestone as the screen contract — not one sprint later.

| Tier | Name | Real money? | Who can use it | Reached at |
| --- | --- | --- | --- | --- |
| **T0** | Demo MVP | ❌ sandbox operators | Internal, investors, design partners | end of M1 |
| **T1** | Closed pilot | ✅ limited amounts | Invited cohort (~100–500 users), staff-supervised | end of M7 |
| **T2** | Public pilot | ✅ | Anyone in the pilot zone, marketed | end of M8 |
| **T3** | Launch | ✅ | Public, scaled, supported | after M9 |

At **every** rung the product must be honest about itself: the app should not
display a capability (SMS confirmation, PIN login, "Send money") that the backend
cannot honour. Today, the customer-facing root page claims *"MTN MoMo — Adapter
Active"* (`src/payday/main.py`). §3.1 shows why that claim is false.

---

## 3. New findings from this reconnaissance

These were **not** in the previous roadmap. They were found by reading the
adapters and reproducing their arithmetic, and they change the priority order:
the money path is not "integrated, needs polish" — it is **not connected at all**,
and two of its formatting rules are wrong for the currencies and operators in use.

### 3.1 LB-8 — the platform cannot move real money: live mode does not exist 🔴

```python
# src/payday/adapters/mtn_momo.py:290
mtn_momo_adapter = MTNMoMoAdapter(use_mock=True)
# src/payday/adapters/orange_money.py:283
orange_money_adapter = OrangeMoneyAdapter(use_mock=True)
```

`ChannelAdapterFactory.get_adapter` returns exactly these two module-level
singletons (`adapters/factory.py`), and `grep -niE "mtn|orange|momo|telco|api_key|subscription" src/payday/core/config.py` returns **nothing** — there is no
environment switch, no credential setting, no base-URL setting anywhere in
configuration.

Consequences:

- Every deposit and withdrawal in every test and every demo takes the
  `if self.use_mock:` branch. **All 133 tests pass because the live branch is
  never executed.**
- The live code paths (token fetch, request signing, callback parsing) are, as
  shipped, **unreachable dead code**. They may be perfect; nobody knows.
- The root landing page advertises "Adapter Active" for both operators. That is
  a demo claim, not a status.

This is the single biggest gap between what the project *appears* to do and what
it does. It is also the cheapest to fix relative to its value: configuration plus
a sandbox run.

### 3.2 LB-9 — three representations of the same amount 🔴

The CFA franc has **no minor unit**: XAF is ISO 4217 exponent **0**, and there is
no centime in circulation [1][2][3]. The codebase nonetheless carries 2 decimal
places everywhere, and each adapter serialises differently:

| Request amount | MTN sends (`str(req.amount)`, `mtn_momo.py:104,182`) | Orange sends (`int(req.amount)`, `orange_money.py:105,182`) | Ledger nets (`transaction_manager.py:144`) |
| --- | --- | --- | --- |
| `1000` | `"1000"` | `1000` | `995.00` |
| `1000.00` | `"1000.00"` | `1000` | `995.00` |
| `1000.55` | `"1000.55"` | **`1000`** | **`995.55`** |
| `1000.99` | `"1000.99"` | **`1000`** | **`995.99`** |
| `1000.005` | `"1000.005"` | **`1000`** | `995.00` |

Reproduced with the shipped expressions; not inferred.

Three defects in one table:

1. **Sub-franc amounts are accepted at the API boundary.**
   `DepositInitiateRequest.amount = Field(..., gt=0)`
   (`src/payday/schemas/transaction.py:11`, `:26`) imposes no whole-unit and no
   scale constraint, so `1000.55` is a valid request for a currency that cannot
   represent it.
2. **Orange truncates.** `int(Decimal("1000.99"))` is `1000`. The operator collects
   1 000 XAF while the ledger records 1 000.99 XAF of value in. Every
   reconciliation against an Orange statement will fail by the truncated residue,
   and the error is silent and biased in one direction.
3. **MTN is sent a decimal string.** `"1000.00"` where the API expects an integer
   amount for a zero-decimal currency [1]; `"1000.005"` is worse. Whether MTN
   rejects, rounds or errors is unknown — which is itself the reason LB-11 exists.

`wallet_engine.calculate_fee` compounds it: fees are
`max((amount * pct).quantize(Decimal("0.01")), MIN_FEE_AMOUNT)`
(`wallet_engine.py:32`). The 25 XAF floor hides the problem on small amounts, but
above it the platform's **own revenue is denominated in sub-franc units** — a
withdrawal of 12 345 XAF at 1 % is a **123.45 XAF fee**, and a 12 345 XAF deposit
at 0.5 % is **61.72 XAF** (verified with the shipped expression). A wallet whose
ledger cannot be reconciled to operator statements to the franc is an audit
finding waiting to happen (§4.2: AML record-keeping is a legal obligation, and
reconciliation is how you prove it).

### 3.3 LB-10 — MSISDN format is wrong for at least one operator 🟠

```python
# src/payday/adapters/mtn_momo.py:40-42 and orange_money.py:40-42 — identical
def _clean_msisdn(self, phone: str) -> str:
    return phone.replace("+", "").strip()
```

Both adapters send `237699123456`. MTN's API takes the MSISDN with country code,
so that is plausibly right for MTN. Orange Money's merchant API documents
`subscriber_msisdn` as the **9-digit local number** — "MSISDN sans 237 côté API"
[4] — so Orange receives a value it should reject, for every transaction.

Also missing: any check that the number belongs to the operator it is being sent
to. A user who types an MTN number into the Orange Money channel gets whichever
outcome the operator chooses — silent failure, or worse, a payment request to an
unintended subscriber. `normalize_cameroon_phone` (`core/security.py`)
deliberately accepts both `6[5-9]` and `2[234]` prefixes without mapping them to
operators.

### 3.4 LB-11 — the integration has never been tested against a real payload 🟠

`grep -rn "requesttopay\|subscriber_msisdn\|webpayment" tests/` → **no results.**

Not one test asserts the JSON that leaves the building. The mock branch returns a
canned response before the payload is built, so LB-9 and LB-10 are invisible to
the suite, and so is any future field-name or header regression. This is the
mechanical reason three real defects survived 133 green tests.

**Fix:** golden-payload contract tests (assert the exact dict for a given
request) plus a sandbox integration run recorded in CI as an opt-in job. Cheap,
and it converts "we think it works" into evidence.

### 3.5 The pattern worth internalising

LB-8/9/10/11 are all the same failure mode: **the system was verified against its
own mocks.** The suite is genuinely strong — 133 tests, negative controls, real
concurrency attacks — but every one of them stops at the process boundary. The
remaining work is dominated by things that only reality can falsify: operator
APIs, SMS delivery, file uploads, PostgreSQL under load, and a regulator reading
the evidence. That is the theme of the sequencing in §6.

---

## 4. What the external research changes

### 4.1 Data protection is now a hard legal deadline that has already passed 🔴

**Law No. 2024/017 of 23 December 2024** on personal data protection is in force.
Its 18-month transition period expired **23 June 2026** [5][6][7] — three months
ago. Obligations that land directly on this codebase:

| Obligation | What it means here |
| --- | --- |
| **Prior authorisation from the Data Protection Authority before processing** (Art. 19) | The wallet must be registered/authorised as a processing operation — before onboarding pilot users, not after. |
| **Prior authorisation before any cross-border transfer** (Art. 32), sender and recipient **jointly liable** | Storing ID images in a US/EU bucket (D6) is not a design preference, it is an authorisation application. Same question for the SMS aggregator and any KYC vendor receiving Cameroonian PII. |
| **Sensitive categories need explicit authorisation** | Biometrics (selfie/liveness, D9) and — per at least one reading — banking transaction data fall in this class [7]. |
| **Register of processing activities, DPO for large-scale processing, DPIAs, breach notification** | Nothing of this exists in the repo today: no consent capture, no privacy policy endpoint, no processing register, no breach runbook, no data-subject access/erasure path. |
| **Penalties** | Administrative fines up to 100 M XAF, up to 1 G XAF and criminal liability for executives [5][7]. |
| **Note on vendor literature** | Some vendors still cite the older Law 2010/013 and "CNIL" [8]. That is out of date. Verify compliance claims against the 2024 law directly. |

**The Authority is not fully operational** (its organisation awaits a presidential
decree [7]). That is a reason to prepare, not to wait: the obligations bind
regardless, and the first thing an examiner asks for is evidence created *before*
the incident.

### 4.2 AML/CFT: a regional regulation with teeth, in a grey-listed country 🔴

- **CEMAC Regulation No. 02/24 (20 December 2024)** replaced the 2016 AML/CFT
  framework: risk-based CDD, mandatory PEP and sanctions screening, suspicious
  transaction reporting, and **10-year record retention** [9].
- **ANIF** receives suspicious transaction reports; a report made by phone must be
  **confirmed in writing within 48 hours** [9].
- **Cameroon is on the FATF grey list** (increased monitoring, most recent FATF
  statement cited 19 June 2026); GABAC's follow-up noted persistent supervision
  gaps [9]. Practical consequence: **examiners audit evidence, not intentions.**
  "We plan to add screening" is not a control.

Nothing in the current system performs sanctions/PEP screening, transaction
monitoring, threshold or velocity rules, or STR workflow. The audit log and
reconciliation engine are a good foundation for the evidence trail, not a
substitute for it.

### 4.3 Licensing: the real long pole, and it is not engineering 🔴

To issue e-money and hold customer balances in CEMAC you are a **payment
institution** (*établissement de paiement*):

- Corporate form: **société anonyme with a board of directors** (OHADA law).
- **Minimum paid-up capital: 500 000 000 XAF**, per COBAC Regulations **R-2019/01**
  (licensing) and **R-2019/02** (capital/prudential) under CEMAC Regulation
  **04/18** on payment services [10][11][12].
- Licensing chain: application to the national monetary authority
  (**MINFI / DGTCFM** in Cameroon), instruction by the approvals committee,
  **conforming opinion of COBAC**, then a ministerial order [12].
- Payment institutions **may not collect deposits** — customer funds are
  ring-fenced (*fonds cantonnés*) [12].
- E-money issuance is explicitly recognised as a payment service under 04/18 [11].

**Two viable paths, and they must be chosen now:**

| Path | Cost / time | Consequence for this codebase |
| --- | --- | --- |
| **A. Licence as a payment institution** | 500 M XAF paid-up capital, months of process, governance, prudential reporting | Full control; the roadmap gains a regulatory-reporting workstream (returns to COBAC/BEAC, capital monitoring). |
| **B. Operate as the technology platform of a licensed institution** (bank, EMF/microfinance, or a licensed payment institution) | Commercial agreement; their licence covers the float | Much faster to real money; requires the partner's compliance to accept our controls, and ring-fencing design. Drives who holds the settlement accounts. |

Either way this is the **critical path**: engineering is ≈ 40–55 days (§5), while
a licence is measured in months and an operator merchant contract has explicit
document preconditions (RCCM registration is required even for Orange Money
merchant onboarding; individual-merchant accounts cap at 5 M XAF/day) [4][13].

### 4.4 Mobile money: reachable, but only through a real commercial onboarding 🔴

- **MTN MoMo Open API**: sandbox is self-provisioned; **production credentials are
  issued by MTN only after KYC/business verification and a signed contract**, and
  disbursement (payout) access gets closer scrutiny than collections [14][15][16].
  Production base URL and credentials come from the partner portal, not the
  developer portal [15][16].
- **Orange Money**: merchant onboarding requires RCCM and full identification;
  the Web Payment API is the standard merchant route (`api-s1.orange.cm`), with
  OAuth2 and HMAC-signed webhooks [4][13].
- **Market**: Orange Money ≈ 52 % / 6.5 M active users, MTN MoMo ≈ 45 % / 5.8 M in
  Cameroon; standard merchant commission 1.5–2 % (OM) and 1.2–1.8 % (MTN);
  settlement to bank in 24–72 h [13]. Two implications: (a) supporting both is
  mandatory, not optional; (b) **the platform fee must be priced above the
  operator's commission** or every transaction loses money — a product decision,
  not an engineering one.
- **Aggregators exist** (CamerPay, Simiz, Y-Note and others bundle both operators
  behind one API and one contract) [4][17]. That is a legitimate MVP accelerator
  and a legitimate long-term dependency risk; §7 D23 records it as a decision.

### 4.5 SMS: priced per message, and gated by sender-ID registration 🟠

- Africa's Talking Cameroon bulk rates: **≈ 12 XAF/SMS (Orange), 18 XAF/SMS
  (MTN)** at entry volume [18]; international aggregators quote roughly
  $0.03–$0.05, with some routes far higher ($0.30+) for OTP-grade delivery [19][20].
- **Sender-ID registration is required and slow**: MTN Cameroon requires
  pre-registration, and provisioning an alphanumeric sender ID can take **up to
  three weeks** [19].

Budget arithmetic for D2/D5: 10 000 notifications/day at 15 XAF is **150 000
XAF/day ≈ 4.5 M XAF/month**. Sending an SMS for every transaction is a
product-financing decision, not a default. Today the code writes both an SMS row
and a push row for every transaction (`notification_service.py`), so the default
is "spend money on every event" — with the added irony that nothing is sent at
all yet.

### 4.6 KYC: verification is document+biometric, not a database lookup 🟠

- Cameroon has **no public government identity-validation API**; MINAT issues the
  national ID and the civil registry is not exposed to third-party integrators
  [21].
- Vendors that cover Cameroon documents: **Smile ID** (Cameroon national ID,
  driver's licence, passport, voter ID, resident ID, travel documents; ISO 27001,
  SOC 2) [8][22]; **Didit** quotes **≈ $0.33 per full KYC** [21]. Both are
  cross-border processors → §4.1 authorisation applies.
- Therefore D7's "manual review" is not the cheap fallback it looks like, and D9's
  selfie/liveness check is a **sourced** capability only.

### 4.7 Distribution: the app store is a gate, not a formality 🟠

Google Play requires **every** app to complete the *Financial features
declaration*, including apps with no financial features at all; mobile payments,
digital wallets and money transfer must be declared, and apps must comply with
local law and provide licence evidence where required [23]. A wallet that cannot
show its regulatory basis risks removal at the worst moment. Non-custodial
wallets are exempt; custodial wallets such as this one are not.

---

## 5. The complete remaining-work catalogue

IDs are stable for tracking. "Gate" = what must be true before starting.

### A. Money path (new — highest priority)

| ID | Work | Why | Size | Gate |
| --- | --- | --- | --- | --- |
| **A1** | **Configurable live mode** (LB-8): adapter settings in `core/config.py` (`MTN_BASE_URL`, `MTN_SUBSCRIPTION_KEY`, `MTN_API_USER`, `MTN_API_KEY`, `MTN_TARGET_ENV`, `MTN_MOCK`, and Orange equivalents), factory builds adapters from settings, fail-closed in production if credentials are absent, mock mode explicitly opt-in | Without it the live path is dead code | 1–2 d | — |
| **A2** | **Whole-XAF money contract** (LB-9): reject non-integer amounts at the API boundary (`multiple_of=1` / quantize + reject), store integer XAF, define the fee rounding rule once, make both adapters serialise identically; migration-free (schema stays `Numeric(14,2)` but the domain guarantee becomes whole francs) | Silent 1-franc losses and unreconcilable statements | 2–3 d | Decision D26 |
| **A3** | **Per-operator MSISDN formatting** (LB-10): MTN `237XXXXXXXXX`, Orange `9-digit local`; prefix→operator mapping; validate channel/operator consistency at request time with a clear error | Every Orange transaction likely fails today | 1 d | — |
| **A4** | **Adapter contract tests** (LB-11): golden payloads for collection/disbursement/status on both operators; header assertions (`X-Reference-Id`, `X-Target-Environment`, auth); callback parsing fixtures | The only mechanism that makes A1–A3 stay fixed | 1–2 d | — |
| **A5** | **Sandbox integration run** (opt-in CI job + a recorded transcript): both operators, collection and disbursement, including a callback | Converts "we think it works" into evidence | 2–3 d | Sandbox credentials |
| **A6** | **Settlement & float model**: who holds the funds, how operator statements are reconciled daily, what happens when float runs out, payout failover | Real money; a wallet that cannot pay out is worse than a closed one | design 2–3 d, build later | §4.3 path chosen |
| **A7** | **Refund/reversal against the operator**: current reversal is ledger-only; define the operator-side compensating transaction | An operator-side refund path must exist before disputes | 2–3 d | D29 |

### B. Security & correctness (existing register)

| ID | Work | Notes |
| --- | --- | --- |
| **B1** | **WS-1 / LB-3 — notification delivery** | Transport abstraction, honest `PENDING→SENDING→SENT→DELIVERED/FAILED`, retry/backoff, DLR webhook, device tokens if D4. ≈ 4–6 d after D1/D3. |
| **B2** | **WS-4 / LB-1 — password + PIN reset** | Three-leg OTP flow, `revoke_all_sessions()` on confirm (WS-2 is done and waiting), 24 h fraud hold per D14, PIN clear per D15. ≈ 4–5 d. |
| **B3** | **WS-3 remaining** — notify owner on throttle breach | Depends on B1. ≈ 0.5 d. |
| **B4** | **WS-6 / LB-2 — KYC upload + review** | Multipart upload, magic-byte validation, EXIF strip, `DocumentStore` abstraction (residency!), admin review with audit. ≈ 5–7 d. |
| **B5** | **WS-2 follow-ups** — per-`jti` session denylist / session listing | Enables single-device logout. ≈ 2–3 d. Deferred deliberately; re-open only if product needs it. |
| **B6** | **Refresh-token rotation with reuse detection** | Steals-and-detects. Only worth doing *with* detection. ≈ 2 d. |
| **B7** | **Admin MFA / step-up authentication** | An ADMIN can suspend users and read the whole ledger; a single stolen password is currently enough. ≈ 3 d. |
| **B8** | **Per-user and per-endpoint rate limits** beyond auth (deposits, withdrawals, KYC upload, admin) | Fraud and DoS surface. ≈ 2 d. |
| **B9** | **Out-of-process task queue (R3)** | In-process queue loses retries on restart. Celery/ARQ + worker deployment. ≈ 3–4 d. Do it with B1 or the retry guarantees are fictional. |

### C. Platform & operations

| ID | Work | Notes |
| --- | --- | --- |
| **C1** | **Unblock CI/CD (R8)** | Repo admin reconnects GitHub with the `workflows` scope; the four workflow files are written and local-only. Blocks C2, C4, and WS-7. **Human action, 15 minutes.** |
| **C2** | **Staging environment** | Deployed API + PostgreSQL 15 + Redis, sized like production, with the same pooling config. Gates WS-7, A5, C4. |
| **C3** | **Secrets management** | Move off `.env.example` lineage; a real secret store, rotation procedure, and no secrets in CI logs. |
| **C4** | **Observability & alerting (D22)** | Nothing exists beyond stdout logging (`core/logging.py`). Needs: structured logs, error tracking, metrics (latency, error rate, queue depth, Redis health, notification failures), alert routes, dashboard, on-call owner. ≈ 3–4 d. |
| **C5** | **Backup + restore rehearsal** | The CD design fails closed without a verified backup; the *restore* has never been tested. A backup you cannot restore is a rumour. ≈ 1–2 d + a rehearsal. |
| **C6** | **DR runbook + RTO/RPO** | Define and test: total loss of the primary region, Postgres corruption, Redis loss, operator outage. |
| **C7** | **Incident response runbook** | Who is paged, what they may do, how customers are told, breach notification within the data-protection law's timeline (§4.1). |
| **C8** | **WAF / DDoS / IP allowlisting** for webhooks and admin | Admin and webhook surfaces deserve network-level controls. |

### D. Compliance & legal (mostly not engineering — and the long pole)

| ID | Work | Notes |
| --- | --- | --- |
| **L1** | **Licensing or partner path (§4.3)** | Decision + months of process. Blocks real money. Start immediately. |
| **L2** | **Data-protection program (Law 2024/017)** | Processing register, prior authorisation application, cross-border transfer authorisations (SMS, KYC vendor, hosting), DPO, consent capture, privacy policy, breach procedure, data-subject rights (access/erasure) endpoints. Engineering portion ≈ 4–6 d. |
| **L3** | **AML/CFT program (CEMAC 02/24)** | Risk-based CDD policy, sanctions/PEP screening (vendor), transaction monitoring rules, STR workflow to ANIF, 10-year retention implementation. Engineering portion ≈ 5–8 d. |
| **L4** | **Consumer protection & disclosures** | Fees shown before confirmation, T&Cs, complaint handling, dispute/refund policy, French-language disclosures. Engineering portion ≈ 2–3 d. |
| **L5** | **Records & retention policy implementation** | 10-year retention (AML) vs storage-limitation and erasure rights (data protection) — needs an explicit, documented reconciliation with legal hold. |

### E. Product gaps (from `FRONTEND_INTEGRATION_GUIDE.md` §4)

| ID | Gap | Tier |
| --- | --- | --- |
| **E1** | **P2P send between wallets** (§4.2) | T2 — the single most-requested wallet feature |
| **E2** | **Bill payments** (§4.2) | T3 |
| **E3** | **Notification categories + read/unread state** (§4.3) | T1 (cheap, high support value) |
| **E4** | **PIN login** (§4.1) + device binding, biometrics | T2 |
| **E5** | **Recipient-name resolution** (§4.7) | Post-launch |
| **E6** | **Referral codes** (§4.4) — currently silently discarded | Post-pilot growth |
| **E7** | **UBA Bank channel** | Phase 2 |
| **E8** | **Profile items, avatars, QR, tiers** (§4.10) | Post-launch |
| **E9** | **French/English localisation of API messages** | T1 — Cameroon is bilingual; error strings are English today |

### F. Client enablement

| ID | Work | Notes |
| --- | --- | --- |
| **F1** | Generated SDKS (Dart/TypeScript) + release attachments | Workflow written, never run (C1). |
| **F2** | Sandbox/staging for the Flutter team with seeded demo data | Unblocks parallel mobile work. |
| **F3** | App-store submissions: Financial features declaration, privacy/data-safety forms, licence evidence (§4.7) | T1/T2 gate. |
| **F4** | Support tooling: find a user, find a transaction, re-send a notification, freeze a wallet — all audited | Operations cannot run without it. |

### G. Assurance

| ID | Work | Notes |
| --- | --- | --- |
| **G1** | **WS-7 / LB-5 load test on PostgreSQL** — out-of-process (k6/Locust), realistic seed per D18, soak ≥ 1 h, report vs D19 SLO | Last, and only real if C2 exists first. |
| **G2** | PostgreSQL-backed runs of the concurrency and chaos suites | The 50-parallel-withdrawal test currently runs on SQLite. |
| **G3** | Independent security review / pen test | Before public money. |
| **G4** | UAT with real users on staging | Before pilot. |

---

## 6. The sequence

Engineering ≈ **40–55 days** for one engineer. Licensing/procurement is measured
in **months**. Therefore the plan starts the slow, non-engineering items in M0,
on day one, in parallel with everything else.

### M0 — Start the clocks (day 0, mostly not engineering)

| Item | Owner | Why now |
| --- | --- | --- |
| C1 workflows scope restored | repo admin | 15 minutes, unblocks the entire CI/CD and staging chain |
| L1 licensing vs partner decision | founder/legal | Months of lead time; blocks real money |
| Operator onboarding begun (RCCM, merchant accounts, MoMo partner application) | founder | Contract + KYC verification are prerequisites for production credentials [14][15] |
| D1 SMS aggregator chosen, sender ID submitted | founder/product | Up to 3 weeks for sender-ID provisioning [19] |
| L2 data-protection counsel engaged, authorisation file started | legal/DPO | Deadline already passed; authorisations are prerequisite to processing |
| D2/D5 SMS event policy + budget | product/finance | Determines the notification design |

**MVP at this point:** the demo that exists today. **Cannot claim:** anything about
delivery, real money, or compliance.

### M1 — Live money path in operator sandbox (**T0 Demo MVP**) · ≈ 5–7 d

A1, A2, A3, A4, A5 (+ D26 fee-rounding decision). Everything else already works
against the mock.

**Exit criteria:** a deposit and a withdrawal complete end-to-end against *both*
MTN and Orange **sandboxes**, with real callbacks, driven entirely by
configuration; the golden-payload tests fail if an adapter's request body or
headers change; amounts are whole francs everywhere and non-integer requests are
rejected with a clear error.

**You now have:** a wallet that demonstrably talks to both real operator APIs in
sandbox, with money-format rules that a finance person can accept.
**You still cannot claim:** real payments, SMS, KYC, password reset.

### M2 — Staging, secrets, observability · ≈ 6–9 d (needs C1)

C2, C3, C4, C5, and G2 (Postgres-backed concurrency/chaos runs).

**Exit criteria:** a staging URL that deploys automatically from `main`; migrations
run on PostgreSQL; a restore from backup has been performed and timed; an alert
fires when error rate or latency crosses a threshold, and reaches a named human.

**You now have:** an environment a pilot could be run on, and evidence that the
pipeline works — the first time any pipeline has run.

### M3 — Deliver notifications for real · ≈ 5–7 d (needs D1/D3, B9)

B1 + B9 + B3.

**Exit criteria:** a real SMS reaches a real Cameroonian handset in staging; the
`Notification` row reflects the provider's actual response and DLR; a failed send
retries and then shows `FAILED` with `last_error`; no PUSH row is created without
a real device token.

**You now have:** OTP delivery — which is the prerequisite for M4.

### M4 — Account recovery (**T1 groundwork**) · ≈ 5–6 d (needs D13–D17)

B2 (+ D14/D15 policy).

**Exit criteria:** the three-leg reset works over real SMS; a reset **provably
evicts every existing session** (WS-2 is already in place to make that true); no
user enumeration; PIN reset available to a user who knows their password.

**You now have:** a user who forgot their password is no longer permanently locked
out — closing the most embarrassing support gap in the product.

### M5 — KYC a compliance officer can work with · ≈ 6–9 d (needs D6–D12)

B4 (+ L2 residency authorisation, which M0 started).

**Exit criteria:** a real ID image is uploaded from a phone, stored per D6 with
encryption at rest and EXIF stripped, reviewed by an admin whose every access is
audited, and purged per D8.

**You now have:** the ability to actually verify a customer — the legal
precondition for letting them hold money.

### M6 — Compliance operating system · ≈ 8–12 d (parallel to M3–M5)

L2, L3, L4, L5 engineering portions: consent capture, privacy endpoints,
sanctions/PEP screening on onboarding, transaction-monitoring rules,
STR export to ANIF, retention/legal-hold implementation, French disclosures.

**Exit criteria:** a named officer can produce, on request, the processing
register, a consent record, a screening result, a monitoring alert and an STR —
all evidence, not intentions.

**You now have:** a platform that can survive an examiner's questions, in a
grey-listed country, under a data-protection law whose deadline has passed.

### M7 — Real-money pilot gate · ≈ 3–5 d engineering + business closure

A6, A7, F4, L1 executed (licence or partner agreement signed), operator
production credentials issued, settlement accounts live, support process staffed,
pilot cohort defined (D27), limits set.

**Exit criteria:** a staff account deposits and withdraws **real francs** through
a production operator account, reconciled to the operator's statement to the
franc, and the money can be found in the ring-fenced account.

**You now have: T1 — a closed pilot with real money.** This is the first point at
which the product is genuinely "working" in the sense that matters.

### M8 — Open the doors (progressively) · ≈ 6–10 d (needs D18–D20)

G1 (WS-7 load test), C6/C7 rehearsed, G3 (security review), G4 (UAT), E3, E9,
F1, F3, D22 alerting signed off with a named on-call owner.

**Exit criteria:** a published capacity ceiling measured on production-like
PostgreSQL against an agreed SLO; documented downtime tolerance; app store
declarations accepted; on-call rota live.

**You now have: T2 — a public pilot** with evidence behind its claims and a
rollback that has been practised.

### M9 — Launch and beyond

E1 (P2P), E4 (PIN login), E5, E6, E7 (UBA), E8, B5/B6/B7 as hardening,
E2 (bills), scaling work driven by M8's measurements.

---

## 7. Decision register

Existing decisions D1–D22 live in `LAUNCH_BLOCKER_ROADMAP.md` §2 and are
unchanged. The research above adds:

| # | Decision | Why it cannot be an engineering call |
| --- | --- | --- |
| **D23** | Direct operator contracts vs an aggregator for the pilot | Aggregators collapse onboarding time and add a dependency, a margin and a data-transfer relationship. Strategic, not technical. |
| **D24** | Licence as a payment institution (500 M XAF) vs operating under a partner's licence | Determines the business model, the timeline, and who holds the float. |
| **D25** | Where KYC documents live, and which processors receive Cameroonian PII | Now a legal authorisation question under Law 2024/017 Art. 32, with joint liability — supersedes the framing of D6. |
| **D26** | Fee-rounding and whole-franc policy: are sub-franc amounts rejected outright, or rounded, and in whose favour? | Finance and customer-trust decision; drives A2 and every reconciliation. |
| **D27** | Pilot cohort: how many users, which city, what transaction limits | Drives D18 volume inputs, support staffing and fraud thresholds. |
| **D28** | Support and dispute model at pilot | Determines F4 and C7 scope. |
| **D29** | Operator-side refund/reversal policy | Money-movement policy; drives A7. |
| **D30** | French/English policy for product and API messages | Cameroon is bilingual; retrofitting localisation after the Flutter app ships is expensive. |
| **D31** | Do we take the operator commission on top, or absorb it? | Determines whether the product is viable at 1.2–2 % merchant commission [13]. |

---

## 8. Risk register (additions)

Existing R1–R8 remain. New:

| ID | Risk | Impact | Mitigation |
| --- | --- | --- | --- |
| **R9** | Licensing/partner path takes longer than engineering | Real money slips by quarters while the code is ready | Start M0 today; keep T0/T1-sandbox as the demo story; pick the partner path if speed dominates |
| **R10** | Data-protection non-compliance (deadline passed, grey-listed country) | Fines to 1 G XAF, executive criminal liability, app-store removal | D2c from day one; authorisations before pilot onboarding; evidence generated as the code is written |
| **R11** | Operator production approval arrives after pilot marketing | Public promise with no capability | Sequence marketing after credentials, not before |
| **R12** | SMS cost at volume (4.5 M XAF/month at 10k/day) | Margin erosion, surprise OPEX | D2/D5 policy in M0; event→channel map in config; measure per-notification cost from M3 |
| **R13** | Aggregator dependency (if D23 chooses one) | Pricing power, uptime, data residency | Keep the adapter abstraction; contract for portability |
| **R14** | Float/liquidity exhaustion on payouts | Failed withdrawals, reputation damage | A6 settlement/float model before real money |
| **R15** | Reconciliation drift from sub-franc amounts (LB-9) | Audit findings, unexplained balances | A2 before any real-money pilot |
| **R16** | The suite's greenness creates false confidence (LB-11 pattern) | Defects like LB-8/9/10 ship | Contract/golden tests at every boundary; sandbox runs in CI; treat "verified against mocks" as unverified |

---

## 9. If capacity is limited: the minimum credible slice

Two engineers, two weeks, in this order:

1. **M0 items that need no engineering** (workflows scope, licensing conversation,
   SMS sender ID, operator onboarding paperwork) — start day one, zero engineering
   cost, longest lead times.
2. **A1 + A2 + A3 + A4** — live-mode configuration, whole-franc contract,
   MSISDN formatting, golden-payload tests. This is the difference between a demo
   and a payments integration, and it is the work that only this codebase can
   block.
3. **C1 → C2 → C4** — a pipeline that runs, a staging URL, and alerting. Without
   it, every subsequent claim ("it works") is unverifiable, which is the failure
   mode this project has already paid for twice.

Deliberately **not** in the slice: P2P, bills, UBA, PIN login, session listing,
localisation. They are real work; none of them is on the path to a working MVP.

---

## 10. Sources

External claims above were read on 2026-09-13. Confirm anything contractual with
the counterparty's current documents.

1. ISO 4217 exponent-0 currencies (XAF/XOF listed) — https://customer-api.getpliant.com/docs/monetary-values
2. Currency decimal-places reference — https://www.localization.guide/currencies
3. "XOF has no minor unit" (same arithmetic for XAF) — https://dev.to/catidegla/xof-has-no-minor-unit-and-always-store-money-in-cents-overcharges-by-100x-3fhk
4. Orange Money vs MTN merchant integration, formats and market shares — https://simiz.io/blog/ouvrir-compte-marchand-orange-money-cameroun
5. Law No. 2024/017 (23 Dec 2024), compliance deadline 23 June 2026, penalties — https://pacmap.dev/regulation/cm-personal-data-protection-2024
6. Same law, obligations and authority — https://www.anove.ai/en/regulations/cameroon-law-2024-017
7. Compliance guidance, DPO, sensitive data, transfers — https://lexafrica.com/2025/10/cameroon-data-protection-law-compliance/
8. Smile ID — Cameroon KYC coverage, ID types — https://smile.id/countries/cameroon and https://smile.id/supported-documents
9. CEMAC Regulation 02/24 (AML/CFT), ANIF, 10-year retention, FATF grey list — https://blog.voveid.com/aml-compliance-in-cameroon-a-2025-guide-for-fintechs-and-regulated-businesses/
10. Payment institutions in CEMAC: SA form, 500 M XAF capital, licensing chain — https://kalieu-elongo.com/le-statut-des-etablissements-de-paiement-en-zone-cemac/
11. CEMAC Regulation 04/18 on payment services (e-money issuance recognised) — https://www.sgg.cg/txts-droit-reg/cemac-reglement-2018-04-services-paiement.pdf
12. Cameroon financial-institution categories, COBAC R-2019/01 and R-2019/02, MINFI/COBAC approval chain, funds ring-fenced — https://dgtcfm.cm/les-etablissements-financiers-agrees-aucameroun/
13. Orange Money merchant onboarding (RCCM required), commissions, settlement — https://simiz.io/blog/ouvrir-compte-marchand-orange-money-cameroun
14. MTN MoMo API — sandbox self-provisioning vs production KYC/contract — https://lobehub.com/skills/africandigitalassetframework-africa-stack-skills-mtn-momo
15. MoMo Open API keys: sandbox vs production, partner portal — https://momodevelopercommunity.mtn.com/how-to-59/understanding-momo-open-api-keys-sandbox-and-production-455
16. MTN go-live process — https://momodeveloper.mtn.co.rw/go-live
17. Aggregators offering both operators behind one API — https://camerpay.biz/blog/accepter-orange-money-site-web-cameroun
18. Africa's Talking Cameroon bulk SMS pricing (CFA 12–18) and sender-ID registration — https://africastalking.com/pricing
19. Cameroon SMS provider comparison, sender-ID provisioning up to 3 weeks, MTN pre-registration — https://www.sent.dm/resources/cameroon-sms-pricing
20. Cameroon SMS routing and per-message pricing — https://smspm.com/en/country/send-sms-cameroon/
21. No public government ID-validation API for Cameroon; verification pricing — https://didit.me/solutions/countries/cameroon/
22. Smile ID supported documents (Cameroon) — https://smile.id/supported-documents
23. Google Play Financial features declaration (mandatory for all apps) — https://support.google.com/googleplay/android-developer/answer/13849271
