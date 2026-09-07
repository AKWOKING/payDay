# PayDay — Frontend Integration Guide

**Version:** 1.0 · **Date:** 2026-09-07
**Contract:** OpenAPI 3.1 · `/openapi.json` · baseline snapshot at `docs/api/openapi-baseline.json`
**Audience:** Flutter mobile, Angular landing page, Angular admin back-office

---

> ## ⚠️ Figma screen mapping is incomplete
>
> This guide maps **every endpoint, payload, error code and flow** against the
> live contract. What it does **not** contain is the mapping to specific Figma
> frames and node IDs — the design file
> ([PayDay UI/UX Design](https://www.figma.com/design/I5wNdk5TDrfvNNOvpyAszM/PayDay-UI-UX-Design))
> requires authentication and was not accessible.
>
> Every place a screen reference belongs is marked:
>
> ```
> <!-- FIGMA: screen name · node-id · notes -->
> ```
>
> Send the screen list (or frame names + node IDs) and these will be filled in.
> Nothing else in this document is a placeholder.

---

## 1. Conventions

### Base URL

| Environment | Base URL |
| --- | --- |
| Local | `http://localhost:8000` |
| Staging | *(set `STAGING_BASE_URL`)* |
| Production | *(set `PRODUCTION_BASE_URL`)* |

All v1 endpoints are prefixed `/api/v1`.

### Success envelope

Every successful response uses the same wrapper — unwrap `data` in your client
layer once, not per call:

```json
{
  "success": true,
  "message": "Operation completed successfully",
  "data": { },
  "timestamp": "2026-09-07T13:59:00.000Z"
}
```

Paginated `data` adds:

```json
{ "items": [], "total": 0, "page": 1, "page_size": 20, "total_pages": 0 }
```

### Error envelope — RFC 7807

**Errors do not use the success envelope.** They are Problem Details:

```json
{
  "type": "https://payday.cm/errors/insufficient-funds",
  "title": "Insufficient Funds",
  "status": 400,
  "detail": "Wallet balance (5000.00 XAF) is less than 20200.00 XAF.",
  "instance": "/api/v1/wallet/withdraw",
  "code": "INSUFFICIENT_FUNDS",
  "extra": {}
}
```

**Branch on `code`, never on `detail`.** `detail` is human-readable prose and
will change; `code` is the stable contract. The `ProblemDetail` schema is
published in `components/schemas`, so generated SDKs give you a typed model.

### Money

XAF is a **zero-decimal currency in practice**, but the API transports amounts as
decimal strings/numbers with 2 places (`"50000.00"`).

> **Never parse amounts into `double` / JS `number` for arithmetic.**
> Use `Decimal` in Dart and `decimal.js` (or integer minor units) in TypeScript.
> Binary floating point cannot represent these values exactly and will drift on a
> reconciliation boundary.

### Authentication

`Authorization: Bearer <access_token>` on everything except the public endpoints
listed in §3.

### Idempotency

`POST /wallet/deposit` and `POST /wallet/withdraw` accept an `idempotency_key` in
the body. **Always send one** — generate a UUID v4 when the user taps the button,
and reuse it for every retry of that same intent. The server enforces uniqueness
with a database index, so a retried request returns the original transaction
rather than creating a second one. This is the difference between a flaky network
and a double withdrawal.

---

## 2. Error Codes

| `code` | HTTP | Meaning | Suggested client behaviour |
| --- | ---: | --- | --- |
| `VALIDATION_ERROR` | 422 | Request body/params invalid | Field errors are in `extra.errors[]` — map to form fields |
| `AUTHENTICATION_FAILED` | 401 | Missing/invalid/expired token | Refresh once, then log out |
| `PERMISSION_DENIED` | 403 | Role insufficient | Hide the affordance; do not retry |
| `USER_NOT_FOUND` | 404 | No such user | |
| `USER_ALREADY_EXISTS` | 409 | Phone already registered | Offer login instead |
| `WALLET_NOT_FOUND` | 404 | No wallet | |
| `WALLET_FROZEN` | 403 | Wallet suspended | Show support contact — see PIN lockout, §4.4 |
| `INSUFFICIENT_FUNDS` | 400 | Balance below amount + fee | Show shortfall; offer deposit |
| `DAILY_LIMIT_EXCEEDED` | 400 | 24h ceiling reached | Show the limit and when it resets |
| `MONTHLY_LIMIT_EXCEEDED` | 400 | Monthly ceiling reached | |
| `INVALID_PIN` | 400 | Wrong PIN | Show remaining attempts (`detail` carries "Attempt N of 5") |
| `PIN_NOT_SET` | 400 | No PIN configured | Route to PIN setup |
| `KYC_REQUIRED` | 403 | KYC not verified | Route to KYC submission |
| `DUPLICATE_TRANSACTION` | 409 | Idempotency key reused | Treat as success; fetch the original |
| `INVALID_STATE_TRANSITION` | 409 | Illegal status change | Refresh the transaction |
| `CHANNEL_NOT_AVAILABLE` | 400 | UBA — Phase 2 | Disable the channel in the picker |
| `INVALID_CHANNEL` | 400 | Unknown channel | |
| `INTERNAL_SERVER_ERROR` | 500 | Unhandled | Generic retry message; log correlation |

---

## 3. Public Endpoints (no auth)

Used by the Angular landing page and by the app before login.

### `GET /api/v1/public/health`
Liveness. `data.status == "UP"`. No database I/O (p95 ≈ 0.43 ms) — safe to poll.

### `GET /api/v1/public/info`
Channel availability for the marketing page:

```json
{ "service": "PayDay e-Wallet", "status": "OPERATIONAL", "version": "1.0.0",
  "active_channels": { "MTN_MOMO": "ACTIVE", "ORANGE_MONEY": "ACTIVE", "UBA_BANK": "PLANNED_V2" } }
```

Drive the channel picker from this rather than hard-coding — UBA flips to active
in Phase 2 with no client release.

### `POST /api/v1/public/fee-calculator`
Powers the real-time fee simulator.

```json
{ "type": "DEPOSIT", "channel": "MTN", "amount": 25000.00 }
```

All three fields are **required** (omitting `channel` is a 422). Response:

```json
{ "amount": "25000.0", "fee": "125.00", "total_charged": "25000.0",
  "net_credited": "24875.00", "currency": "XAF", "fee_percentage": 0.5 }
```

Note the inconsistent decimal formatting (`"25000.0"` vs `"125.00"`) — amounts
are serialised from `Decimal` and echo the input's scale. Another reason to parse
into a decimal type rather than string-compare or display raw.

Fee model: deposit 0.5% (deducted from the credit), withdrawal 1.0% (added to the
debit), minimum 25 XAF. Deposits charge `amount` and credit `amount − fee`;
withdrawals debit `amount + fee`.

<!-- FIGMA: landing page / fee simulator · node-id · notes -->

---

## 4. Flutter Mobile — Customer Journey

### 4.1 Registration

`POST /api/v1/auth/register`

```json
{ "full_name": "Jean-Luc Kamdem", "phone_number": "677889900",
  "email": "jeanluc@example.cm", "password": "SecretP@ssword123",
  "id_document_no": "108273948", "id_document_type": "NATIONAL_ID" }
```

- `phone_number` is normalised server-side — `677889900`, `+237677889900` and
  `237677889900` all resolve to the same account. Display the normalised value
  returned by the server.
- `password` minimum 8 characters. `email` optional.
- `id_document_no` is **encrypted at rest (AES-256-GCM)** and never returned in
  plaintext — only a mask.

> **Privilege fields are ignored.** Sending `role` or `kyc_status` has no effect;
> new users are always `CUSTOMER` / `PENDING`. Verified by test.

<!-- FIGMA: registration screen · node-id · notes -->

### 4.2 Login and tokens

`POST /api/v1/auth/login` → `{ "phone_number": "...", "password": "..." }`

```json
{ "access_token": "...", "refresh_token": "...", "token_type": "bearer",
  "expires_in": 1800, "user_id": "...", "role": "CUSTOMER",
  "has_pin": false, "kyc_status": "PENDING" }
```

`has_pin` and `kyc_status` come back on login precisely so you can route
immediately — send the user to PIN setup or KYC without an extra round trip.

Access tokens last 30 minutes. On a 401 with `AUTHENTICATION_FAILED`, call
`POST /api/v1/auth/refresh` with `{ "refresh_token": "..." }` **once**; if that
also fails, clear credentials and return to login.

> Store tokens in `flutter_secure_storage` (Keychain / EncryptedSharedPreferences),
> never in `SharedPreferences`.

<!-- FIGMA: login screen · node-id · notes -->

### 4.3 KYC

- `POST /api/v1/kyc/submit` — `{ "id_document_no": "...", "id_document_type": "NATIONAL_ID" }`
- `GET /api/v1/kyc/status` — returns `kyc_status`, `id_document_masked`, `verified_at`

Statuses: `PENDING` → `VERIFIED` | `REJECTED` (with `rejection_reason`).
Transacting while unverified returns `KYC_REQUIRED`.

The API only ever returns a masked document number. If you need to show it, show
the mask — the plaintext is not retrievable through the API by design.

<!-- FIGMA: KYC submission + pending/verified/rejected states · node-id · notes -->

### 4.4 Transaction PIN

`POST /api/v1/auth/set-pin` → `{ "pin": "4417", "password": "..." }`

The current password is required to set or change the PIN. The PIN is bcrypt
hashed, never returned.

> **Five consecutive wrong PINs freeze the wallet** and dispatch a security
> alert. `detail` carries "Attempt N of 5" — surface the remaining count so the
> freeze is never a surprise. After freezing, calls return `WALLET_FROZEN` and
> only an admin can restore access.

<!-- FIGMA: PIN setup + PIN entry + lockout warning · node-id · notes -->

### 4.5 Wallet

| Endpoint | Returns |
| --- | --- |
| `GET /api/v1/wallet/balance` | `balance`, `locked_balance`, `currency` |
| `GET /api/v1/wallet/me` | Full wallet: status, limits, balances |

> **Show `balance` as available and surface `locked_balance` separately.**
> Funds held for an in-flight withdrawal sit in `locked_balance`. If you display
> only `balance`, a customer mid-withdrawal sees money that appears missing.
> Label it "pending" rather than hiding it.

<!-- FIGMA: wallet home / balance card · node-id · notes -->

### 4.6 Deposit (cash-in)

`POST /api/v1/wallet/deposit`

```json
{ "channel": "MTN", "amount": 40000.00, "phone_number": "+237677001122",
  "idempotency_key": "<uuid-v4>" }
```

Returns **202 Accepted** with `transaction_id`, `external_ref`, `status`.

**202 does not mean settled.** The telco must confirm. The flow is:

```
POST /wallet/deposit ──► 202, status=PENDING|PROCESSING
                          │
        customer approves on their MoMo/OM handset
                          │
   provider ──► POST /api/v1/webhooks/{mtn|orange} ──► status=SUCCESS, balance credited
```

Client handling: show a "confirm on your phone" state, then poll
`GET /api/v1/wallet/transactions/{transaction_id}` (2–3 s, backing off, ~2 min cap)
until `status` leaves `PENDING`/`PROCESSING`. Never optimistically credit the UI.

<!-- FIGMA: deposit amount → channel → confirm → pending → success/failure · node-id · notes -->

### 4.7 Withdrawal (cash-out)

`POST /api/v1/wallet/withdraw`

```json
{ "channel": "ORANGE", "amount": 30000.00, "destination_phone": "+237699887766",
  "pin": "4417", "idempotency_key": "<uuid-v4>" }
```

`pin` is **required**. Returns 202; same webhook-settled lifecycle as deposit.

Funds move to `locked_balance` at initiation and are debited on settlement. If
the telco fails or times out, the hold is released automatically and the balance
returns — verified under sustained outage and concurrency in Sprint 4. So a
failed withdrawal briefly shows reduced available balance before it restores;
poll to final state before telling the user anything definitive.

Total debited is `amount + fee` (1.0%, min 25 XAF). Show the total, not just the
amount.

<!-- FIGMA: withdrawal amount → destination → PIN → pending → result · node-id · notes -->

### 4.8 History and receipts

- `GET /api/v1/wallet/transactions?page=1&page_size=20&tx_type=&channel=&tx_status=`
- `GET /api/v1/wallet/transactions/{transaction_id}` — full receipt

Note the query parameter names: **`page_size`** (not `size`), and
**`tx_type` / `tx_status`** (not `type` / `status`).

Statuses: `PENDING` → `PROCESSING` → `SUCCESS` | `FAILED`, plus `REVERSED` for
admin reversals. Render `REVERSED` distinctly — the customer's balance moved back
and an unexplained reversal generates support load.

<!-- FIGMA: transaction list + receipt detail · node-id · notes -->

### 4.9 Linked accounts

- `GET /api/v1/wallet/linked-accounts`
- `POST /api/v1/wallet/linked-accounts` — `{ "provider": "MTN", "account_identifier": "+237677001122", "is_default": true }`

Accounts are also auto-created on first use of a number in a deposit/withdrawal.

<!-- FIGMA: linked accounts management · node-id · notes -->

### 4.10 Notifications

`GET /api/v1/notifications?page=1&page_size=20&channel=`

Transaction alerts and security notices (including PIN-lockout).

<!-- FIGMA: notifications list · node-id · notes -->

---

## 5. Angular Admin Back-Office

All endpoints require `ADMIN` (reconciliation also allows `AUDITOR`). A
`CUSTOMER` token receives `PERMISSION_DENIED`; no token receives 401. Both are
enforced and tested.

### 5.1 Users and KYC review

- `GET /api/v1/admin/users?page=&page_size=&search=&role=`
- `POST /api/v1/admin/users/{user_id}/status?status_val=&reason=` — **query parameters, not a body**
- `POST /api/v1/kyc/review/{user_id}` — `{ "status": "VERIFIED" | "REJECTED", "rejection_reason": "..." }`

<!-- FIGMA: admin user list + KYC review queue · node-id · notes -->

### 5.2 Wallet controls

- `POST /api/v1/admin/wallets/{wallet_id}/status` — `{ "status": "FROZEN", "reason": "..." }`
- `PUT /api/v1/admin/wallets/{wallet_id}/limits` — `{ "daily_limit": 500000.00, "monthly_limit": 5000000.00 }`

Wallet freeze is also the recovery path for a PIN lockout (§4.4).

<!-- FIGMA: wallet freeze / limits · node-id · notes -->

### 5.3 Transactions and reversals

- `GET /api/v1/admin/transactions?page=&page_size=&tx_type=&channel=&status=&search=`
- `POST /api/v1/admin/transactions/{transaction_id}/reverse` — `{ "reason": "...", "admin_notes": "..." }`

> **Reversal is irreversible and single-shot.** Only `SUCCESS` transactions can
> be reversed; a second attempt on the same transaction is refused with
> `INVALID_STATE_TRANSITION` and does not double-debit (tested). Reversing a
> deposit whose funds have since been spent fails with
> `INSUFFICIENT_FUNDS_FOR_REVERSAL`.
>
> Require a typed confirmation in the UI and always show the reason field — this
> writes to the immutable audit log.

<!-- FIGMA: transaction search + reversal confirmation · node-id · notes -->

### 5.4 Settlement reconciliation

`POST /api/v1/admin/reconcile`

```json
{ "channel": "MTN", "start_date": "2026-09-07", "end_date": "2026-09-07",
  "partner_records": [
    { "external_ref": "MTN-MOMO-5002663C", "amount": 5000.00,
      "currency": "XAF", "status": "SUCCESS", "channel": "MTN" }
  ] }
```

Every `partner_records` item requires `external_ref`, `amount`, `status` **and
`channel`** — omitting `channel` is a 422.

Response carries `total_internal_transactions`, `total_internal_volume`,
`matched_count`, `mismatches_count` and the mismatch list, typed as
`AMOUNT_MISMATCH`, `STATUS_MISMATCH`, `MISSING_IN_INTERNAL`, `MISSING_IN_PARTNER`.

> `MISSING_IN_PARTNER` means PayDay settled something the telco never reported.
> **Do not offer a one-click reverse from this screen** — it requires provider
> confirmation first. Link to the transaction instead.

<!-- FIGMA: reconciliation dashboard + variance table · node-id · notes -->

### 5.5 Audit log

`GET /api/v1/admin/audit-logs?page=&page_size=&action=&entity_name=`

Append-only: actor, action, entity, before/after state, IP. Read-only in the UI —
there is no mutation endpoint by design.

<!-- FIGMA: audit log viewer · node-id · notes -->

---

## 6. Generated SDKs

Do not hand-write HTTP clients. `.github/workflows/generate-client-sdks.yml`
produces both on every API change:

| Client | Generator | Artefact |
| --- | --- | --- |
| Flutter | `dart-dio` | `payday-sdk-dart` |
| Angular ×2 | `typescript-angular` | `payday-sdk-typescript` |

Download from the workflow run, or from release assets on a tagged release.

The contract is guarded: removing an operation, or adding a newly-required field
to an existing request schema, **fails CI** (`test_sprint4_contract_drift.py`).
Both are breaking for shipped mobile clients that cannot be rolled back.

---

## 7. Local Development

```bash
uvicorn payday.main:app --host 0.0.0.0 --port 8000   # or: docker compose up --build
```

Swagger UI `/docs` · ReDoc `/redoc` · Spec `/openapi.json`

### CORS

The API uses an **explicit allowlist**, not `*`. Allowed by default:
`http://localhost:4200`, `http://127.0.0.1:4200`, `http://localhost:3000`,
`http://localhost:8000`.

Serving Angular from another port? Add it to `BACKEND_CORS_ORIGINS` in `.env`.
Do **not** set `["*"]` — combined with credentials that reflects any origin and
was a real vulnerability fixed in Sprint 4.

CORS does not affect Flutter — it is browser-enforced only.

### Simulating telco callbacks

Without real provider credentials, drive settlement yourself:

```bash
curl -X POST http://localhost:8000/api/v1/webhooks/mtn \
  -H 'Content-Type: application/json' \
  -d '{"transaction_id":"<id>","external_ref":"<ref>","status":"SUCCESSFUL"}'
```

`/api/v1/mock-telco/{mtn|orange}/simulate-callback` does the same with provider-
shaped payloads. Both are sandbox aids — real webhooks are HMAC-SHA256 verified
with anti-replay protection.

---

## 8. Integration Checklist

- [ ] Unwrap the `data` envelope once, centrally
- [ ] Branch on `code`, never `detail`
- [ ] `Decimal` for all money — never `double` / JS `number`
- [ ] Send an `idempotency_key` on every deposit and withdrawal
- [ ] Treat 202 as *initiated*; poll to a final status
- [ ] Show `locked_balance` as pending, never hide it
- [ ] Surface remaining PIN attempts before the 5-attempt freeze
- [ ] Refresh on 401 exactly once, then log out
- [ ] Tokens in secure storage
- [ ] Drive the channel picker from `/public/info`
- [ ] Render `REVERSED` transactions distinctly
- [ ] Use the generated SDKs
- [ ] **Fill in the `<!-- FIGMA: … -->` screen references**
