# Research — Obtaining MTN MoMo and Orange Money API access (Cameroon)

**Date:** 2026-10-04 · **Author:** Lead System Architect
**Question asked:** what will it take for us to obtain MoMo and OM API access?
**Short answer:** the API contracts are the *easy* part. The hard part is that a wallet
that holds customer balances is a licensed activity in CEMAC, and since May 2025 the
Minister of Finance has been enforcing that — including a rule that licensed providers
**must not partner with unlicensed payment providers**. So this is not a procurement
task with a form to fill in; it is a licensing/partnership decision that the API
contracts sit on top of.

**Evidence quality is marked on every claim**, because the sources disagree in places:
`[operator]` operator's own portal/documentation · `[regulator]` regulation, Ministry or
COBAC/BEAC text · `[legal]` law-firm analysis · `[partner]` integrator or developer
report · `[vendor]` aggregator marketing (commercial interest — treat as a lead, not a fact).

---

## 1. What we are actually asking for

Both operators sell *products*, not "API access". We need three, and they are not
granted together:

| Capability | MTN product | Orange product | Notes |
| --- | --- | --- | --- |
| Take money from a customer | Collections — RequestToPay `[operator]` | Web Payment (webpay) `[operator]` | Both documented; both need approval |
| Send money to a customer | Disbursements — Transfer `[operator]` | Payout / "Remboursement (CASHOUT)" — **separate contract** `[partner]` | Orange's payout is not part of the webpay merchant offer |
| Async status notification | Callback + status endpoint `[operator]` | `notif_url` + transaction status `[operator]` | Already implemented on our side (A8) |

**The Orange finding matters most for our code.** Our adapter assumes a payout path
(`ORANGE_PAYOUT_PATH`, default `"payout"`) and treats it as the same contract as
collections. The evidence says payouts are a **distinct Orange product with their own
contract and document set** — the integrator contract lists a separate "API de
Remboursement (CASHOUT)" requiring a signed and counter-initialled service contract
`[partner]`. Until we confirm it, our Orange withdrawal path is unverified in a second
sense: not just the endpoint, but whether we will be granted the product at all.

---

## 2. The gate that decides everything: may PayDay hold balances?

PayDay's product stores customer value — wallet balances, and now P2P transfers between
them. Under CEMAC law that is e-money issuance, and it is a licensed activity:

* Payment services may be provided only by **credit institutions, microfinance
  institutions, or licensed payment institutions** (Règlement n° 04/18/CEMAC/UMAC/COBAC,
  art. 5) `[regulator]`.
* A **payment institution** needs an agrément from the national Monetary Authority
  (MINFI in Cameroon) after a **conforming opinion from COBAC** and a **BEAC technical
  opinion**; it must be a **public limited company with a board**, with **minimum paid-up
  capital of 500,000,000 XAF** (COBAC R-2019/02, art. 2), a COBAC-approved general manager
  and statutory auditor, and — for a mobile/USSD channel — an authorisation from the
  telecom regulator; the technical solution must satisfy interoperability requirements
  (GIMAC) `[regulator]` `[legal]`.
* Issuing e-money specifically is reserved to that status: "a standard PSP licence does
  not authorise this" — stored value requires the e-money/payment-institution route
  `[legal]`.

### Enforcement is live, not theoretical

* **MINFI communiqué, 5 May 2025** `[legal]`: companies operating fund collection, money
  transfer, lending or other payment services **without approval** were given **three
  months** to obtain payment-institution status, failing which they face **closure by
  order** under **Article 84** of Regulation 04/18. The same communiqué instructs
  **licensed payment service providers, businesses and public administrations to cease
  all partnerships with unlicensed payment providers**, and reserves the right to
  prosecute promoters.
* **Order 080/CAB of 28 May 2025** tightened the definition of electronic means of
  payment, raising the bar for anyone holding customer funds `[legal]`.
* The regional reform now in preparation (not yet in force) would add an explicit
  category for **payment initiation / account information operators that hold no customer
  funds — regulated by COBAC *authorisation* rather than full agrément**, with a
  **regulatory sandbox** (up to 12 months, renewable) for innovative providers, and would
  give currently-unlicensed providers a 12-month compliance window `[legal]`. If it lands
  as drafted, it opens a genuinely lighter route for aggregation-style services.

**Consequence for us:** an aggregator or an operator API contract gives us *rails*, not
permission to hold balances. Because licensed PSPs are explicitly barred from partnering
with unlicensed payment providers, "use an aggregator and keep the wallet" reproduces the
same licence problem one layer down. The regulatory question has to be answered first;
the API contracts then follow the chosen structure.

### Who is licensed, and the two proven market-entry precedents

* Licensed payment institutions in Cameroon: **Orange Money Cameroun S.A.** (first, 2022;
  capital raised — its 2026 terms state 2,464,280,000 XAF) and **Mobile Money Corporation**
  (MTN MoMo, licensed 2023). **Konoom Cameroun** became the third, by arrêté n°681/MINFI of
  21 July 2026 on COBAC decision D-2026/092 `[legal]` `[regulator]`.
* **Precedent A — operate under a licensed bank (Wave)**: COBAC decision D-2025/122 of
  11 June 2025 authorised **Commercial Bank Cameroun** to provide the "Wave" service in
  partnership with Wave Transfer S.A., after a BEAC non-objection; Wave holds no licence of
  its own. The bank's request was dated **25 June 2024** — roughly **12 months** from
  request to authorisation `[legal]`. Note the quirk: because no new payment institution
  was licensed, MINFI was not part of that procedure.
* **Precedent B — obtain the agrément yourself**: Konoom, licensed July 2026, having
  previously done the same in Chad in October 2025 `[legal]`.

**COBAC is watching the middle ground.** At its 25 September 2026 meeting it flagged that
customer funds "sometimes pass directly" through the accounts of payment institutions and
**aggregators**, and called for stronger traceability. No new rules were announced — but a
route built on funds sitting in our own accounts is the exact thing under scrutiny
`[legal]`.

---

## 3. Route 1 — MTN MoMo API (entity: Mobile Money Corporation)

**Where the contract is:** Mobile Money Corporation Limited (MMC), MTN Cameroon
headquarters, 360 Rue Drouot, BP 15574 Douala. The Cameroon API terms are MMC's, and the
stated contact for anything beyond the standard API limits is **MoMoCorporate.CM@mtn.com**
`[operator]`.

**Steps (from MTN's own onboarding documentation)** `[operator]`:

1. Create a developer account on `momodeveloper.mtn.com` and subscribe to the products.
   **Sandbox needs no business registration** and credentials are self-provisioned —
   we have already done this.
2. Test against the sandbox and **retain the test evidence**. "Apply and go live with the
   business KYC **and test results** specific to the country your business will operate in";
   MTN's community guidance is to share test results with them during review.
3. In the portal choose **Go-Live** → select the operating country (**Cameroon**) → choose a
   package and product set → provide KYC information for the business owner and the business.
4. **Download, complete and submit the KYC documents together with a signed contract**,
   through the portal, for vetting and approval.
5. On approval, MTN grants access to the **Partner Portal** and creates an account on the
   production developer portal (`momoapi.mtn.com`) using the email we supply.
6. In the Partner Portal, **create the API User and API Key** per product. Sign-in
   credentials (e.g. `username.sp1`) are shared one-to-one by the account manager, and the
   account needs a **registered MTN SIM that can receive an OTP** — so we need a Cameroonian
   MTN number in the company's control.
7. Register the **callback host** (`providerCallbackHost`) when creating API keys, and take
   the production subscription key from the production portal.

**Technical deltas from what we run today:**

* `X-Target-Environment` becomes **`mtncameroon`** (already configurable, and we refuse to
  start live without it).
* The production base URL is **issued, not published**. Two candidates appear in the field:
  `api.mtn.com` in secondary write-ups, and — more credibly — **`proxy.momoapi.mtn.com`**,
  which is the host a partner used in a real production API-user call that returned 404
  `[partner]`. Our configuration deliberately has **no default for production**, which is
  the right call; this is exactly the value we must take from onboarding and never guess.
* Collections and disbursements use **separate credentials and subscription keys** —
  already modelled correctly in our adapter.
* Disbursements need **float**: the payout wallet must hold enough balance, and mature
  integrations query `GET /disbursement/v1_0/account/balance` before batching payouts
  precisely to avoid cascading failures `[partner]`. We do **not** do that yet, and it is a
  real operational requirement for withdrawals, not an optimisation.

**Timeline evidence:** MTN's Uganda tenant advertises go-live through the Open API in
"approximately 10 days as opposed to over 2 months previously" `[operator]`, but that is a
different country's page and marketing copy. Public partner reports show friction —
production API-user 404s, a dead KYC form link on the Nigerian path, and collections
accepted with no SMS reaching customers `[partner]`. **Plan on weeks-to-months, not days,
and assume one clarifications round.**

---

## 4. Route 1 (continued) — Orange Money API (entity: OMCM)

**The merchant status comes first.** Orange is explicit: the service is for **merchants who
are KYA-compliant**, and "companies that hold the Orange Money merchant status can access
this service" `[operator]`. Practically, a startup must register as an Orange Money
merchant — "either by registering on developer.orange.com/apis/om-webpay, **or by
registering in an Orange store of the country where you operate**… In both instances, you
need to **sign a contract**" `[operator]`. Cameroon offers two merchant profiles:
**Marchand Distributeur** (physical collection) and **Marchand En ligne** (web/mobile/API)
`[vendor]` — we need the second.

**Documents.** Orange Cameroon's own partner page lists `[operator]`:
RCCM (company registration), carte de contribuable, valid patente, CNI of the managing
director or a person with power of attorney, bank attestation (RIB), and the company
statutes. Integrator documentation adds the documents that actually cause rejections: an
**NIU that is current**, a **bank account in the company's exact name** (a RIB in the
manager's name is refused), **statutes registered and annotated at the Commercial Court**,
and proof of address (commercial lease or ENEO bill) `[partner]` `[vendor]`. Aggregate
guidance: **seven documents**, 5–15 working days, and a submitted dossier is verified for
consistency between RCCM, NIU and RIB `[vendor]` (aggregator source — treat the timeline as
indicative).

**Fees and caps reported:** opening/access fee **40,000–50,000 XAF** `[vendor]` `[partner]`;
merchant commission **1.5–2%** vs MTN's **1.2–1.8%** `[vendor]`; merchant daily cap
**20,000,000 XAF** (an individual account is capped at 2,000,000) `[vendor]`.

**Credentials at the end** `[operator]`: a **merchant key** issued by Orange, and an
**Authorization header** (client_id/client_secret, base64 for Basic) from the app on
developer.orange.com; token endpoint `/oauth/v3/token`; per-country API paths
(`/orange-money-webpay/cm/v1/…`) with a `/dev/v1` test environment. **Tests must be
validated by Orange's teams before production deployment**, and production details are sent
by email once tests pass `[partner]`.

**Open questions for Orange:** whether a payout/disbursement product is available to an
online merchant at all in Cameroon, on what contract, and with what float model; and —
because their API is mid-migration — which generation of the transaction-status endpoint
our merchant contract will use.

---

## 5. Route 2 — be the technology, not the licence-holder

PayDay keeps building the platform; a licensed institution is the PSP of record and the
funds custodian. This is the Wave/CBC shape: a bank licensed as a PSP extending its
authorisation to a service built by a partner, with COBAC's prior authorisation and a BEAC
non-objection — about **12 months** end-to-end `[legal]`.

* **Candidates:** the two incumbents are competitors as much as partners; the realistic
  route is a **commercial bank or a microfinance institution** (378 licensed EMFs in
  Cameroon `[legal]`) that wants a wallet product without building it. Konoom is a third
  possibility as a licence-holder seeking distribution.
* **What changes in our code:** little. Our ledger becomes a sub-ledger that must
  reconcile to the licensee's safeguarded accounts, and the audit trail becomes evidence
  the licensee relies on. The interface we already built (single settlement authority,
  conservation by transfer group, operator-verified status) is exactly what such a partner
  would want to see.
* **What we must accept:** the licensee owns the customer relationship and the float; the
  economics are a revenue share, not float income.

---

## 6. Route 3 — own the licence

500,000,000 XAF paid-up capital, an SA with a board, COBAC-approved management, approved
statutory auditor, GIMAC interoperability certification, telecom authorisation for the
mobile channel, safeguarding of float at a CEMAC credit institution, prudential reporting,
external audits, and a 10-year record regime `[regulator]` `[legal]`. Even with a perfect
dossier the procedural clocks alone (BEAC up to 3 months for its technical opinion, COBAC
3 months to decide) put this at **6–12 months at best**, and legal analyses put a payment
institution at 6–12 months and full e-money issuance higher still `[legal]`.

**Source conflict, flagged rather than smoothed over:** one aggregator blog presents a
table with "EME 500M XAF / EP 200M XAF" and a 9–15 month e-money timeline `[vendor]`. The
**500M XAF minimum for payment institutions** is what the COBAC prudential regulation,
Cameroon's DGTCFM and multiple law-firm analyses all state; the **200M figure appears
nowhere in primary sources**. Plan on 500M.

---

## 7. The aggregator option — what it is and is not

**It is a real answer to "get to market this quarter."** CamerPay, CinetPay, Notch Pay,
Monetbil and DigitalisPay all offer one REST integration covering MTN and Orange, signed
webhooks, and days-scale onboarding; DigitalisPay advertises **2.5% cash-in / 0% cash-out**
`[vendor]`. Flutterwave lists Cameroon mobile-money collections at **2%** and mobile-money
payouts at **1%**, but also documents a **XAF payout restriction** with "no timeline for
lifting" `[vendor]` — that must be clarified in writing before it is relied upon for
withdrawals.

**It is not a licence to hold customer balances.** Three reasons: the activity is reserved
to licensed institutions `[regulator]`; licensed PSPs were instructed to stop partnering
with unlicensed payment providers `[legal]`; and COBAC is now specifically examining funds
that transit aggregator accounts `[legal]`. Aggregators that describe themselves as
"licensed principals" are offering *their* licence for a pass-through flow — which can
legitimately cover accepting payments for our merchants, but not PayDay holding customer
value in a wallet.

**One disqualifier, noted:** a provider offering settlement in **crypto** `[vendor]` sits
against BEAC/COBAC rules that bar institutions from facilitating crypto transactions
`[regulator]`. Do not build a treasury dependency on it.

---

## 8. What this means for the product (the uncomfortable part)

| Product shape | Route | Time | What it costs us |
| --- | --- | --- | --- |
| **Non-custodial**: merchant checkout + payouts to customer wallets; PayDay never holds a balance | Own merchant contracts, or an aggregator | Weeks | **We lose the wallet.** No stored balances, no P2P between PayDay users (the feature we just shipped), no float. The site's "wallet" claim becomes false. |
| **Custodial under a licensed partner** | Contract with a bank/PI + BEAC/COBAC filings | ~6–12 months | Revenue share; the partner owns the customer relationship and float |
| **Custodial under our own licence** | Own agrément | 12–24 months, 500M XAF | Capital, governance, audit burden — but full control |

There is no fourth option where we hold balances without one of these. That is the
decision in front of the company, and it is a founders/board decision, not an engineering
one. Engineering's job is to keep the platform capable of all three — which it currently
is.

---

## 9. Company documents to assemble now (they gate every route)

Available in 1–5 days each, and blocking for both operators
`[operator]` `[partner]` `[vendor]`:

| # | Document | Source | Notes |
| --- | --- | --- | --- |
| 1 | **RCCM** (company registration extract) | Commercial Court | Mandatory; Orange will not proceed without it |
| 2 | **NIU** + current tax regularity certificate | DGI / impots.cm | A lapsed NIU is a standard rejection reason |
| 3 | **Statutes**, registered and annotated | Notary + Commercial Court | Unregistered statutes are refused |
| 4 | **Patente** | Communal authority | Must be valid for the current year |
| 5 | **Company bank account (RIB) in the company's exact name** | Bank | A RIB in the manager's name is refused |
| 6 | **CNI/passport of the managing director** (+ power of attorney if a different person manages the account) | — | Both operators ask; Orange asks for all names on the RCCM |
| 7 | **Proof of address**: commercial lease or ENEO bill | Landlord / ENEO | Required by Orange; useful to MTN |
| 8 | **A registered MTN SIM in the company's name** | MTN | Needed to receive the Partner Portal OTP |
| 9 | **A public HTTPS domain** owned by the company, for the callback host | Registrar | MTN validates the callback host against what we register |
| 10 | **Corporate documents for the licensee route**: cap table, board resolution, AML policy, DPIA/DPO details | — | Needed by any bank/PI partner, and later by COBAC |

---

## 10. Technical prerequisites — what we already have vs still owe

| Prerequisite | State |
| --- | --- |
| Sandbox integration with both operators, driven by configuration | **Done** (M1 A1–A4) |
| Parsing the operators' real callback shapes, verified settlement, sweep for lost notifications | **Done** (A8/A9) |
| Whole-franc money handling, per-operator MSISDN formats | **Done** |
| Live mode that fails closed rather than guessing hosts | **Done** |
| **Retained sandbox test evidence** | **Owed** — MTN asks for test results as part of the go-live dossier. This promotes A5 from engineering hygiene to an onboarding deliverable |
| **Public HTTPS callback host on a company domain** | **Owed** — blocked on the domain/hosting decision |
| **Disbursement float, and a balance check before payouts** | **Owed** — our withdrawal path has no float awareness |
| **Orange payout product confirmation** | **Owed** — payouts may be a separate contract we have not asked for |
| Merchant/production credentials, `X-Target-Environment=mtncameroon`, production base URL | Blocked on approval |

---

## 11. The first-contact script (send to both operators and any partner)

Getting these answers in the first exchange saves weeks:

1. Confirm the entity we contract with: MMC for MTN MoMo, OMCM for Orange Money.
2. **Collections** — which product, what fee (percentage and any fixed fee), settlement
   timing and to where (bank account, in XAF)?
3. **Disbursements/payouts** — is this available to us, on which contract, with what float
   requirement and what limits? *(For Orange: is payout part of webpay or a separate
   agreement?)*
4. Onboarding: the exact document list, the acceptance point (agency vs portal), and the
   expected end-to-end lead time.
5. API credentials and environments: production base URL, `X-Target-Environment`, which
   OAuth/authorisation model, and the **current** transaction-status endpoint generation.
6. Callback host: what registration is required, and where the callback source IPs are
   published (we would allowlist if provided).
7. Limits: per-transaction, daily and monthly, for both directions.
8. **Regulatory status question, asked plainly:** do you require us to hold a payment
   institution agrément (or operate under a licensed partner's authorisation) to run a
   wallet that stores customer balances and moves money between our own users? If yes, can
   you contract with a technology provider that operates under a licensed partner?
9. For an aggregator: confirm in writing whether Cameroon XAF **payouts** are enabled for
   our account today, and that settlement is in XAF (not crypto).

---

## 12. Recommendation, in order

1. **Decide the custody route this month** (founders/board): non-custodial now, or
   partner-licensed wallet, or go for our own agrément. Everything else is downstream, and
   the frontend's claims must match whichever is chosen — a wallet we cannot legally offer
   is LB-14 all over again.
2. **Assemble documents 1–9 in §9 immediately.** They cost little, take days, and gate
   every route including the fastest one.
3. **Open both operator conversations in parallel** with the script in §11: MTN via the
   partner-portal Go-Live flow with the first email to `MoMoCorporate.CM@mtn.com`, Orange
   via Orange Business (Douala Bonanjo / Yaoundé Hippodrome) or `developer.orange.com`.
   Two contracts are slower than one aggregator and materially cheaper at scale.
4. **Shortlist two partner-licensed institutions** for Route 2 and start one conversation,
   because it is the only route that preserves the product we have built — and it has the
   longest lead time of the three.
5. **Do not hold customer balances** until the route is settled. Until then, operator
   sandbox and mock modes remain the honest environment.
6. **Keep the engineering work aimed at the same target**: whatever the route, the
   deliverables are the same — operator-verified settlement, provable conservation,
   retained test evidence, a public callback host, and float awareness.

---

## 13. Sources

1. MTN MoMo API FAQ — credentials, sandbox vs Partner Portal, callback host, go-live steps — `[operator]` https://momoapi.mtn.com/content/html_widgets/28s8q.html
2. MTN MoMo production configuration (Partner Portal, forms, production subscription keys) — `[operator]` https://momodevelopercommunity.mtn.com/how-to-59/momo-api-production-configuration-101
3. MTN "How to become a MoMo API Developer" — go-live with KYC and country test results; Cameroon in the country list — `[operator]` https://momodevelopercommunity.mtn.com/getting-started-in-the-community-2/how-to-become-a-momo-api-developer-197
4. MTN "Open a business account with MoMo" — KYC documents and contract — `[operator]` https://momodevelopercommunity.mtn.com/getting-started-in-the-community-2/open-a-business-account-with-momo-from-mtn-232
5. MTN Cameroon API terms — contracting entity MMC, contact MoMoCorporate.CM@mtn.com — `[operator]` https://momodeveloper.mtn.com/Cameroon_apitermsandconditions
6. MTN production portal — `proxy.momoapi.mtn.com` in a real production call — `[partner]` https://momodevelopercommunity.mtn.com/momo-api-production-q-a-7
7. MTN Open API go-live timing claim (Uganda) — `[operator]` https://www.mtn.co.ug/helppersonal/mtn-open-api/
8. MTN disbursement float check before batch payouts — `[partner]` https://github.com/sublime247/mobile-money/issues/1967
9. Orange Money Web Payment / M Payment API — merchant KYA, contract requirement, Cameroon supported, integration partners — `[operator]` https://developer.orange.com/apis/om-webpay
10. Orange Money Web Payment FAQ — merchant status, startup route via an existing merchant, production after tests — `[operator]` https://developer.orange.com/apis/om-webpay/faq
11. Orange Cameroon "Partenaire paiement entreprise" — administrative document list — `[operator]` https://www.orange.cm/fr/om-partenaires/partenaire-paiement-entreprise.html
12. Orange Money merchant onboarding in Cameroon (documents, timelines, fees, caps, profiles) — `[vendor]` https://simiz.io/blog/ouvrir-compte-marchand-orange-money-cameroun
13. Orange Money Cameroon contract (Accesseur cahier des charges, access fee, tests validated by Orange, separate cashout API documents) — `[partner]` https://www.y-note.cm/wp-content/uploads/2023/12/conditions-particulieres-du-service-Orange-Money-de-paynote-comme-moyen-de-paiement-1.pdf · https://www.y-note.cm/faq-paynote-api/
14. Orange Money Cameroon merchant documents and addressing (French) — `[partner]` https://viaziza.com/en/comment-accepter-le-paiement-orange-money-sur-son-site/
15. Règlement n° 04/18/CEMAC/UMAC/COBAC on payment services — licensed categories, authorisation procedure, telecom authorisation — `[regulator]` http://kalieu-elongo.com/wp-content/uploads/2019/05/Règlement-du-21-décembre-2018-relatif-aux-services-de-paiement-dans-la-CEMAC.pdf
16. COBAC R-2019/02 — 500M XAF minimum capital, SA with board — `[regulator]` via https://primetimelawoffice.com/how-to-obtain-payment-service-license-cameroon/ and https://dgtcfm.cm/les-etablissements-financiers-agrees-aucameroun/
17. MINFI communiqué, 5 May 2025 — three-month deadline, Article 84 closure, ban on partnerships with unlicensed providers — `[legal]` https://droitmediasfinance.com/index.php/actualites/droit-tech-fintech/1002-cameroun-le-ministre-des-finances-interdit-les-fintechs-et-les-plateformes-de-paiement-exercant-sans-agrement-prealable
18. Order 080/CAB of 28 May 2025 and PSP vs EMI comparison (timelines, safeguarding) — `[legal]` https://globallawexperts.com/payment-service-provider-vs-electronic-money-issuer-cameroon/
19. CEMAC payments-reform draft — new operator categories, COBAC authorisation vs agrément, sandbox, 12-month window — `[legal]` https://www.droitmediasfinance.com/index.php/actualites/droit-tech-fintech/1298-cemac-une-reforme-en-cours-de-la-reglementation-des-services-de-paiements-agregateurs-initiateurs-de-paiement-transfert-d-argent-bac-a-sable-reglementaire
20. Wave / Commercial Bank Cameroun — COBAC D-2025/122, bank partnership model, request-to-authorisation timeline — `[legal]` https://www.droitmediasfinance.com/index.php/actualites/droit-tech-fintech/1022-cameroun-la-cobac-accorde-lautorisation-prealable-a-commercial-bank-cameroun-pour-son-nouveau-service-de-paiement-wave
21. Konoom Cameroun agrément (arrêté 681/MINFI, 21 July 2026; third payment institution) — `[legal]` https://www.agenceecofin.com/actualites-finance/1709-141667-cameroun-konoom-obtient-son-agrement-et-se-lance-sur-le-marche-du-paiement-mobile
22. COBAC 25 September 2026 — customer funds through aggregator accounts under scrutiny — `[legal]` https://www.businessincameroon.com/finance/2809-16848-cemac-regulator-tightens-scrutiny-of-customer-funds-in-mobile-money-transactions
23. Licensed financial institutions in Cameroon (banks, EMFs, payment institutions; 500M XAF) — `[regulator]` https://dgtcfm.cm/les-etablissements-financiers-agrees-aucameroun/
24. Aggregator landscape and fees (CamerPay, CinetPay, Notch Pay, Monetbil) — `[vendor]` https://www.njokalab.com/insights/how-to-integrate-mtn-momo-orange-money-cameroon-api/ · https://digitalispay.com/
25. Flutterwave Cameroon pricing and XAF payout restriction — `[vendor]` https://flutterwave.com/cm/pricing · https://flutterwave.com/us/support/general/about-the-payout-pause-in-cameroon
26. Aggregator CEMAC licensing table (conflicts with primary sources on capital) — `[vendor]` https://simiz.io/blog/marche-paiement-mobile-cemac-2026
