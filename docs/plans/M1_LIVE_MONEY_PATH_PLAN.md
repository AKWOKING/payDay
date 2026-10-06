# M1 — Live Money Path: Implementation Plan

**Date:** 2026-09-13 · **Milestone:** M1 of `docs/MVP_EXECUTION_ROADMAP.md`
**Goal:** a deposit and a withdrawal complete end-to-end against **both** MTN and
Orange operator APIs, driven entirely by configuration, with money rules a
finance person can accept.
**Status:** plan approved for execution; A1–A4 implemented this step, A5/A8 next.

---

## 1. Research inputs (read 2026-09-13; sources in §8)

| Question | Finding | Design consequence |
| --- | --- | --- |
| MTN endpoints | `POST {base}/collection/token/`, `POST {base}/collection/v1_0/requesttopay`, `GET .../requesttopay/{ref}`, `POST {base}/disbursement/token/`, `POST {base}/disbursement/v1_0/transfer`, `GET .../transfer/{ref}`, refund at `POST /disbursement/v1_0/refund` | Collections and disbursements share a base URL but **not credentials** |
| MTN credentials | Sandbox is self-provisioned; production is issued after KYC/contract. **Collection and disbursement each have their own API user, API key and subscription key** (Vodacom/MTN reference implementations pass them as separate config blocks) | Config must carry two credential sets per operator, not one |
| MTN auth | `POST .../token/` with Basic `apiUser:apiKey` + `Ocp-Apim-Subscription-Key`; `access_token` valid ~1 hour | Token must be cached **with expiry**; the current cache is unbounded |
| MTN request | `amount` is a **string**; `currency` (`XAF`); `externalId`; `payer`/`payee`.`partyIdType=MSISDN`; `partyId` = E.164 without `+` (`237XXXXXXXXX`); `payerMessage`; `payeeNote`; headers `X-Reference-Id` (UUID), `X-Target-Environment` (`sandbox` \| `mtncameroon`), subscription key, optional `X-Callback-Url` | Amount serialised as a whole-number string; MSISDN keeps the country code |
| MTN callback | Body carries `externalId`, `amount`, `currency`, **`transactionStatus`**, `financialTransactionId` — *not* our internal field names. No HMAC signature. Independent guidance: **"never trust callback data alone; verify by calling the API"** | Callback parsing + authoritative status re-query is mandatory (A8), not optional hardening |
| Orange auth | `POST https://api.orange.com/oauth/v3/token`, `grant_type=client_credentials`, Basic header. Two documented deployment styles: Orange-issued **pre-encoded** Authorization header, or `client_id`/`client_secret` you encode yourself | Support both; never hand-build an unencoded Basic header |
| Orange endpoints | Web Payment: `POST https://api.orange.com/orange-money-webpay/cm/v1/webpayment` (prod), `.../dev/v1/webpayment` (sandbox). Response `{status, message, pay_token, payment_url, notif_token}` | Base URL and path belong in config, not code |
| Orange request | `merchant_key`, `currency` (`XAF`), `order_id` (unique), `amount`, `reference`, `notif_url` (**absolute URL**), `lang`; `subscriber_msisdn` is the **9-digit local** number in reseller documentation | Per-operator MSISDN; `notif_url` needs a public base URL setting |
| Orange status | Status endpoint varies by API generation (`transactionstatus/{order}`, `transaction/{pay_token}`, `mp/paymentstatus/…`), and a 2026 migration notice says the previous WebPaiement cash-in API is being decommissioned | Endpoint must be configurable and **confirmed in the sandbox** (A5 gate) |
| Amount ambiguity | Orange docs show `amount` both as a number and as a string across versions; MTN documents a string | Send the documented type per operator, pin it with a golden test, and confirm Orange's in the sandbox |
| Currency | XAF is ISO 4217 **exponent 0** — no centimes exist | Every amount on the wire and in the ledger is a whole franc |

## 2. Codebase review — what the current code actually does

| Finding | Evidence |
| --- | --- |
| Both adapters are mocks in production | `mtn_momo.py:290`, `orange_money.py:283` — `use_mock=True` singletons returned by `factory.get_adapter()` |
| No telco configuration exists | `grep -niE "mtn\|orange\|momo\|api_key\|subscription" src/payday/core/config.py` → nothing; `.env.example` has no telco keys |
| Token cache never expires | `if self._cached_token: return self._cached_token` (`mtn_momo.py:49`, `orange_money.py:49`) — breaks after ~1 hour in production |
| Orange OAuth is doubly wrong | `POST {base_url}/oauth/token` — Orange's token host (`api.orange.com`) is not the Web Payment host, and `Authorization: Basic {client_id}:{client_secret}` is missing base64 (`orange_money.py:52-61`) |
| Orange default base URL is not a documented Orange host | `https://api.orange.cm/orange-money-webpay/dev/v1` (`orange_money.py:32`) |
| `notif_url` is relative | `f"{settings.API_V1_STR}/webhooks/orange"` → `/api/v1/webhooks/orange` (`orange_money.py:109`); Orange cannot call that |
| No `X-Callback-Url` on MTN requests | Absent from both MTN payload paths |
| Amounts serialise three different ways | MTN `str(req.amount)`, Orange `int(req.amount)` (truncates), ledger `quantize("0.01")` |
| MSISDN handling is identical for both operators | `_clean_msisdn` = `phone.replace("+","")` in both adapters |
| Nothing tests the outbound payload | `grep -rn "requesttopay\|subscriber_msisdn\|webpayment" tests/` → nothing |
| Tests post our *internal* webhook shape | `WebhookCallbackPayload(external_ref=…, status=…)` is what the endpoints accept (`webhooks.py`, `schemas/transaction.py:74`), which no operator sends |

## 3. Design decisions

### D-A1. Configuration, and failing closed
New `TelcoSettings` in `core/config.py`, one block per operator per product.
`TELCO_MODE` ∈ `mock` | `sandbox` | `live`, defaulting to **`mock`** so existing
tests and local dev are unchanged.

Validation rule (mirrors the WS-0 fail-closed precedent): if
`ENVIRONMENT == "production"` and `TELCO_MODE != "live"`, the application must
**refuse to start**, because a production deployment running the mock adapter
would silently accept deposits that never happen. If `TELCO_MODE == "live"` and
any required credential for an enabled channel is missing, also refuse to start.

Adapters are constructed from settings by the factory (lazily, so tests can
monkeypatch settings), and remain injectable for tests.

### D-A2. Token caching with expiry
Cache `(token, expires_at)` and refresh `TOKEN_REFRESH_SKEW_SECONDS` (default 300)
before expiry. On a 401 from a business endpoint, drop the cache and retry once.
This is the single most likely production-only failure in the current code.

### D-A3. Payload building is a pure function
`build_requesttopay_payload(...)`, `build_transfer_payload(...)`,
`build_webpayment_payload(...)` are pure module-level functions returning dicts.
Tests assert them directly (A4) — no HTTP, no mocks of HTTP. This is the
mechanism that makes LB-9/LB-10 regressions impossible to reintroduce.

### D-A4. Money: whole francs end-to-end
- **API boundary:** `amount` gains `multiple_of=Decimal("1")`, so `1000.55` is a
  422 rather than a silent rounding. Verified: pydantic 2.13 rejects it for both
  float and string input with `multiple_of`.
- **Fees:** one authority, `core/money.py::whole_xaf()`, applied to every
  computed amount. Rounding mode is configurable
  (`XAF_ROUNDING_MODE`, default `HALF_UP`) because **D26 (fee-rounding policy) is
  still an open product decision** — the code makes the policy explicit and
  flip-able rather than silently implying one. `MIN_FEE_AMOUNT` is applied after
  rounding, so the floor stays a whole number.
- **Wire format:** MTN `str(whole)`, Orange `int(whole)` — the same *value* in
  each API's documented *type*. (An earlier framing said "identical
  serialisation"; the research shows the two APIs document different types, so
  the guarantee is identical **value**, per-API type, pinned by golden tests.)

### D-A5. MSISDN: format per operator, do not hard-block on prefix
- MTN receives `237XXXXXXXXX` (E.164, no `+`).
- Orange receives the 9-digit national number.
- **Prefix→operator inference is unreliable**: sources disagree on the `68x`
  range, and Cameroon has number portability (the prefix reflects the original
  allocation, not the current network). Therefore:
  - reject **non-mobile** numbers (national `2xxxxxxxx` fixed lines) for both
    operators — a landline can never receive mobile money;
  - treat a prefix/operator mismatch as a **warning by default**, with an opt-in
    strict mode (`TELCO_STRICT_OPERATOR_PREFIX=true`) for teams that want a hard
    reject;
  - keep the table sourced and in one place (`core/msisdn.py`).

  Hard-rejecting on a stale prefix table would fail legitimate ported numbers —
  a worse outcome than routing them and letting the operator answer.

### D-A6. Scope boundary for this step
A1, A2, A3, A4 ship now. **A8 (provider webhook parsing + authoritative status
requery) and A5 (sandbox run) are the next step**, because A8 is a distinct
change to the callback contract and A5 needs credentials nobody has yet.

## 4. Task breakdown for this step

| Task | Deliverable | Verification |
| --- | --- | --- |
| **A1** | `TelcoSettings` (per-operator, per-product), `TELCO_MODE`, fail-closed startup validation, factory builds live adapters, token cache with expiry + one retry on 401 | Startup-refusal tests for `production+mock` and `live+missing credentials`; token-expiry unit test with a frozen clock |
| **A2** | `core/money.py::whole_xaf` + `XAF_ROUNDING_MODE`; `multiple_of` on deposit/withdraw amounts; fees whole; both adapters send whole francs | API-level 422 for `1000.55`; parametrised rounding tests; golden payloads show integers |
| **A3** | `core/msisdn.py` with per-operator formatting, landline rejection, warning/strict prefix policy | Unit tests per operator + a ported-number case |
| **A4** | Golden payload tests for both operators, both directions: exact dict, headers, URL path, and the amount/MSISDN formats | New test module; deliberately fails if a payload field changes |

## 5. What this step will *not* prove

- That either operator accepts these payloads. **Only A5 can prove that** — the
  sandbox run, with credentials, recordable as evidence.
- That Orange's `amount` type and status endpoint are current (docs are
  ambiguous and their API is mid-migration). A5 carries an explicit checklist
  item for both, to be confirmed against the live sandbox response rather than
  assumed.
- Real callbacks end-to-end (A8).

## 6. Risks introduced or retired

| Risk | Note |
| --- | --- |
| R17 — mis-formatting a live payment request | Retired for the two known defects by A2/A3/A4; residual risk is Orange's ambiguous `amount` type, which A5 confirms |
| R18 — token expiry in production | Retired by D-A2 |
| R19 — a production deploy accidentally running mocks | Retired by the fail-closed startup rule |
| R20 — over-strict prefix validation breaking ported numbers | Mitigated by D-A5 (warn by default, strict opt-in) |

## 7. Implementation status (updated as the work lands)

| Task | Status | Evidence |
|---|---|---|
| A1 — fail-closed telco configuration | **Done** | `core/config.py`: `TelcoSettings` block, `TelcoConfigurationError`, `validate_telco_configuration()`, called from `main.py` at startup. Probes: mock defaults load; `production`+`mock` refused; `live`+missing creds refused naming each setting; `live` refused unconditionally until A8. |
| A2 — real token acquisition + expiry-aware cache | **Done** | Both adapters request a token at the configured host, cache it keyed by product with the expiry minus `TELCO_TOKEN_REFRESH_SKEW_SECONDS`, and retry once on 401. Orange now uses `ORANGE_TOKEN_URL` (a *different host* from the API) and base64-encodes `client_id:client_secret` when no pre-issued header is configured. |
| A3 — pure payload builders | **Done** | `build_requesttopay_payload`, `build_transfer_payload`, `build_webpayment_payload`, `build_payout_payload`, `map_status` are pure functions with no I/O; the HTTP layer consumes them. `X-Callback-Url` sent on collections; Orange `notif_url` is absolute. |
| A4 — golden-payload tests | **Done** | `tests/test_sprint7_money_path.py` — 47 tests: exact request dicts for both operators (deposit + withdraw), MSISDN formatting, whole-franc money, config fail-closed rules, the A8 interlock, token expiry, base64 encoding, and the public status surface. |
| A1–A4 regression check | **Done** | Full suite **180 passed, 2 skipped** (133 pre-existing + 47 new). Fee probe: 12 345 XAF → 62 deposit / 123 withdraw, whole francs. |
| A4 negative control | **Done** | Mutating `build_requesttopay_payload`/`build_transfer_payload` to send `str(amount)`, and `build_webpayment_payload`/`build_payout_payload` to send `int(amount)`, makes all four golden-payload tests fail (`'15000.00' != '15000'`, `20000 != 20001`, `5000 != 5001`); restoring the source makes them pass. The control also caught a real gap: the transfer, webpayment and payout goldens originally used whole amounts, so they passed under truncation — they now use fractional amounts that must round **up** (8000.60→8001, 20000.60→20001, 5000.60→5001). |

**Not done — and therefore not claimed anywhere:**

| Task | Why it is open |
|---|---|
| A5 — sandbox run against the real operator sandboxes | Needs MTN sandbox credentials self-provisioned on the Partner Portal and Orange dev credentials, plus a public HTTPS callback URL. Until it runs, every endpoint, header, status path and the Orange `amount` type are *pinned by tests, not confirmed by the operators*. |
| A8 — callback verification + authoritative status requery | Not implemented: `verify_webhook_signature` rejects in live mode and `validate_telco_configuration()` refuses to start live. This is deliberate; the money path stays shut until a verified callback contract exists. |
| LB-8…LB-11 registration follow-through | Verified defects are registered in `docs/LAUNCH_BLOCKER_ROADMAP.md`; LB-8…LB-11 move to "fixed pending sandbox confirmation" only after A5. |

## 8. Sources (read 2026-09-13)

1. MTN MoMo API reference: endpoints, headers, payload, callback fields — https://lobehub.com/skills/africandigitalassetframework-africa-stack-skills-mtn-momo
2. MTN Collections technical reference (token, 202 semantics, error codes) — https://medium.com/@bmskmike/mtn-mobile-money-momo-request-to-pay-api-complete-technical-reference-for-nigerian-developers-4c148732dceb
3. MTN Go SDK: per-product endpoints and status payload fields — https://pkg.go.dev/github.com/NdoleStudio/mtnmomo-go
4. MTN PHP SDK: separate collection vs disbursement credential blocks — https://github.com/patricpoba/mtn-momo-api-php
5. "Validate callbacks via API, never trust callback data alone" — https://dev.to/lepresk/how-to-integrate-mtn-mobile-money-in-php-complete-guide-j81
6. Production/pilot endpoints and `X-Callback-Url` usage — https://cleverengineer.substack.com/p/going-live-with-mtn-momo-api-in-2025
7. Cameroon target environment and XAF — https://kolonell.com/en/blog/mtn-momo-api-ghana-ivory-coast-2026
8. Orange Web Payment: OAuth host, endpoints, payload, response, status check — https://www.y-note.cm/comment-deployer-lapi-orange-money/
9. Orange merchant onboarding, OAuth 2.0, HMAC webhooks, 4–8 s confirmation, idempotency — https://simiz.io/blog/ouvrir-compte-marchand-orange-money-cameroun
10. Orange Web Payment SDK (response shape, status endpoint, sandbox host) — https://github.com/Foris-master/orange-money-sdk
11. Orange API migration notice (WebPaiement cash-in being decommissioned) — https://www.paynote.africa/mise-a-jour-technique-orange-money-guide-complet-de-migration-pour-vos-integrations-om-webpaiement/
12. Cameroon mobile prefixes by operator (+ portability caveat) — https://dialingcodes.me/en/cm.html · https://www.mycountrymobile.com/country-code/cameroon/
13. XAF ISO 4217 exponent 0 — https://customer-api.getpliant.com/docs/monetary-values
