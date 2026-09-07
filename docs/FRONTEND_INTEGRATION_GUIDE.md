# PayDay — Frontend Integration Guide

**Version:** 2.0 · **Date:** 2026-09-07
**Contract:** OpenAPI 3.1 · `/openapi.json` · baseline snapshot at `docs/api/openapi-baseline.json`
**Design:** [PayDay UI/UX Design](https://www.figma.com/design/I5wNdk5TDrfvNNOvpyAszM/PayDay-UI-UX-Design?node-id=0-1&m=dev)
**Audience:** Flutter mobile, Angular landing page, Angular admin back-office

---

> **Read §4 before estimating any mobile work.** Nineteen screens were reviewed
> against the live contract. Most map cleanly, but **six designed features have
> no backend to call** — including the login method on the Login screen. Those
> are listed with severity in the gap register.
>
> Frames are referenced by **name** as they appear on the Figma canvas. Node IDs
> are not included: they can't be read from a design export, and a wrong deep
> link is worse than none. Anyone with Dev Mode access can add them to the
> tables in §3.

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

Every successful response uses the same wrapper — unwrap `data` once, centrally:

```json
{
  "success": true,
  "message": "Operation completed successfully",
  "data": { },
  "timestamp": "2026-09-07T13:59:00.000Z"
}
```

Paginated `data` adds: `{ "items": [], "total": 0, "page": 1, "page_size": 20, "total_pages": 0 }`

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

**Branch on `code`, never on `detail`.** `detail` is human prose and will change;
`code` is the stable contract. `ProblemDetail` is published in
`components/schemas`, so the generated SDKs give you a typed model.

### Money and currency

The API transports amounts as decimals with 2 places and always reports
`"currency": "XAF"`.

> **The designs use two labels for one currency.** Dashboard and Withdrawal
> screens read **FCFA** (`FCFA 1,450,000`, `50,000 FCFA`); the receipt and
> success screens read **XAF** (`50,000 XAF`). These are the same money — XAF is
> the ISO 4217 code, FCFA the colloquial local name. Pick one and apply it
> everywhere, or users will believe they are different currencies. Recommend
> **FCFA** in-app for Cameroonian users, **XAF** on receipts and exports.
> Either way, drive it from a single localisation constant, not from the API
> field.

> **Never parse amounts into `double` / JS `number` for arithmetic.** Use
> `Decimal` in Dart and `decimal.js` (or integer minor units) in TypeScript.

Note the API echoes the input scale, so you will see both `"25000.0"` and
`"125.00"` in one response. Another reason to parse into a decimal type rather
than display the raw string.

### Authentication

`Authorization: Bearer <access_token>` on everything except the public endpoints
in §5.

### Idempotency

`POST /wallet/deposit` and `POST /wallet/withdraw` accept `idempotency_key`.
**Always send one** — generate a UUID v4 when the user taps the button and reuse
it for every retry of that intent. The server enforces uniqueness with a database
index, so a retry returns the original transaction instead of creating a second
one. This is the difference between a flaky network and a double withdrawal.

---

## 2. Error Codes

| `code` | HTTP | Meaning | Client behaviour |
| --- | ---: | --- | --- |
| `VALIDATION_ERROR` | 422 | Invalid body/params | Field errors in `extra.errors[]` — map to form fields |
| `AUTHENTICATION_FAILED` | 401 | Missing/invalid/expired token | Refresh once, then log out |
| `PERMISSION_DENIED` | 403 | Role insufficient | Hide the affordance |
| `USER_NOT_FOUND` | 404 | No such user | |
| `USER_ALREADY_EXISTS` | 409 | Phone already registered | Offer login |
| `WALLET_NOT_FOUND` | 404 | No wallet | |
| `WALLET_FROZEN` | 403 | Wallet suspended | Show support contact (see PIN lockout) |
| `INSUFFICIENT_FUNDS` | 400 | Balance below amount + fee | Show shortfall; offer deposit |
| `DAILY_LIMIT_EXCEEDED` | 400 | 24h ceiling reached | Show limit and reset time |
| `MONTHLY_LIMIT_EXCEEDED` | 400 | Monthly ceiling reached | |
| `INVALID_PIN` | 400 | Wrong PIN | `detail` carries "Attempt N of 5" — surface it |
| `PIN_NOT_SET` | 400 | No PIN configured | Route to **PIN confirmation page** |
| `KYC_REQUIRED` | 403 | KYC not verified | Route to **Verify Account 1** |
| `DUPLICATE_TRANSACTION` | 409 | Idempotency key reused | Treat as success; fetch the original |
| `INVALID_STATE_TRANSITION` | 409 | Illegal status change | Refresh the transaction |
| `CHANNEL_NOT_AVAILABLE` | 400 | UBA — Phase 2 | Disable in the channel picker |
| `INVALID_CHANNEL` | 400 | Unknown channel | |
| `INTERNAL_SERVER_ERROR` | 500 | Unhandled | Generic retry message |

---

## 3. Screen Inventory

Nineteen frames, in build order.

| # | Figma frame | Primary endpoint(s) | Status |
| ---: | --- | --- | --- |
| 1 | Onboarding Page | *(none — static)* | ✅ |
| 2 | Splash Welcome | `GET /public/health` (optional reachability) | ✅ |
| 3 | Registration Page | `POST /auth/register` | ⚠️ §4.4, §4.5 |
| 4 | PIN confirmation page | `POST /auth/set-pin` | ⚠️ §4.6 |
| 5 | Account Success page | *(none — post-register state)* | ✅ |
| 6 | Login | `POST /auth/login` | 🛑 **§4.1** |
| 7 | Dashboard | `GET /wallet/balance`, `GET /wallet/transactions`, `GET /public/info` | ⚠️ §4.2 |
| 8 | Deposit Page | `GET /public/info` (provider availability) | ✅ |
| 9 | Amount Deposit Page | `POST /public/fee-calculator`, `POST /wallet/deposit` | ⚠️ §4.9 |
| 10 | Withdrawal Page | `GET /wallet/balance`, `POST /public/fee-calculator`, `POST /wallet/withdraw` | ✅ |
| 11 | Transfer Successful | `GET /wallet/transactions/{id}` | ⚠️ §4.7 |
| 12 | Receipt Page | `GET /wallet/transactions/{id}` | ⚠️ §4.7, §4.8 |
| 13 | Main – Modal Confirmation | `GET /wallet/transactions/{id}` | ⚠️ §4.7, §4.8 |
| 14 | Transaction History | `GET /wallet/transactions` | ⚠️ §4.2 |
| 15 | Notifications Feed | `GET /notifications` | 🛑 **§4.3** |
| 16 | User Profile | `GET /auth/me`, `GET /kyc/status` | ⚠️ §4.10 |
| 17 | Verify Account 1 | `POST /kyc/submit` | 🛑 **§4.5** |
| 18 | Verify Account 2 | `POST /kyc/submit` | 🛑 **§4.5** |
| 19 | *(Dashboard “Send” action)* | — | 🛑 **§4.2** |

✅ buildable as drawn · ⚠️ buildable with a documented adjustment · 🛑 blocked on backend work

---

## 4. Design ↔ API Gap Register

Six blockers and seven adjustments. Each needs a decision — change the design, or
build the endpoint.

### 4.1 🛑 Login screen uses PIN; the API has no PIN login

**Screen:** Login
**Design:** Mobile Number `+237 6XX XXX XXXX`, then **“Access by PIN”** with four
PIN dots, plus a **“Forgot PIN?”** link.

**API:** `POST /auth/login` accepts **`phone_number` + `password`** only. The PIN
is a *transaction* PIN, set later via `POST /auth/set-pin` — which itself
*requires the account password*. No endpoint authenticates a PIN.

There is also **no password-reset or PIN-reset endpoint anywhere** in the API, so
“Forgot PIN?” has nothing to call. (A `ChangePasswordRequest` schema exists in
`schemas/auth.py` but **no route uses it**.)

**Options**

| Option | Work | Note |
| --- | --- | --- |
| **A.** Change the design to password login | Frontend only | Ships today. Worse UX on mobile. |
| **B.** Add PIN login to the API | Backend: new endpoint + rate limiting | Matches the design. **PIN login must reuse the 5-attempt lockout**, or it becomes a 4-digit bypass of an 8-character password. |
| **C.** Password login once, then device-local PIN unlock | Frontend + secure storage | Common wallet pattern. PIN gates a locally-stored refresh token; the server contract is unchanged. **Recommended.** |

Option C gets the designed UX with no backend change. Either way, **a
password-reset endpoint is still required** before launch — today a user who
forgets their password is permanently locked out.

### 4.2 🛑 Dashboard “Send”, bill payments, and bank transfers have no endpoints

**Screens:** Dashboard, Transaction History

The API has exactly two money movements: **DEPOSIT** (cash-in from MoMo) and
**WITHDRAW** (cash-out to MoMo). The designs show three things outside that:

| Design element | Where | Backend reality |
| --- | --- | --- |
| **“Send”** button | Dashboard, next to “Reload” | No P2P transfer endpoint |
| **“Supermarket Bill −45,000 FCFA”** | Dashboard, Recent Activity | No merchant/bill payment endpoint |
| **“Bank Transfer · Deposit · +100,000 XAF · Pending”** | Transaction History | UBA is **Phase 2**; the channel factory returns `CHANNEL_NOT_AVAILABLE` |

“Reload” and the Deposit/Withdraw/History quick actions map fine. **“Send” does
not.** Decide whether it is out of MVP scope (hide it) or whether P2P transfer is
a backend deliverable — it is a substantial one, since an internal wallet-to-wallet
transfer needs its own ledger path, fee model and reversal semantics.

The transaction model also has **no description or merchant field**, so a row can
never read “Supermarket Bill”. History rows can only show type, channel, amount,
fee and status.

### 4.3 🛑 Notifications Feed cannot be built as drawn

**Screen:** Notifications Feed

| Design element | Backend reality |
| --- | --- |
| Tabs **All / Transactions / System** | `GET /notifications?channel=` filters by **SMS / PUSH** — the *delivery* channel, not a category. No category field exists. |
| **“Unread”** badge on a notification | **No read state exists.** `Notification` has `channel`, `recipient`, `message`, `status` (PENDING/SENT/FAILED), `sent_at`. Nothing more. |
| **“Mark all as read”** | No endpoint, and nothing to mark. |
| **“Review actions”** link on a security alert | No action/deep-link field on the model. |

`status` is *delivery* status, not read status — do not repurpose it, or a failed
SMS will render as an unread badge.

**Required backend work:** add `category` (TRANSACTION / SECURITY / SYSTEM),
`read_at`, and a `POST /notifications/read` (or per-item `PATCH`). Until then the
screen can render a flat, undifferentiated list only.

### 4.4 ⚠️ Registration collects a Referral Code the API discards

**Screen:** Registration Page (“Step 1 of 3”)

`RegisterRequest` accepts `full_name`, `phone_number`, `email` (optional),
`password`, `id_document_no`, `id_document_type`. **There is no `referral_code`
field** — it would be silently dropped, which is worse than not collecting it.

Either add the field to the API, or remove it from step 1. Do not ship a form
input that goes nowhere.

Step 1 collects Full Name, Email, Referral Code. Phone number and password must
be collected in steps 2–3 — **all fields go in a single `POST /auth/register`**
at the end of the wizard, not per step.

### 4.5 🛑 KYC screens upload images; the API accepts a string

**Screens:** Verify Account 1, Verify Account 2

**Design:** Document Type dropdown (National ID Card), **Upload Front**, **Upload
Back**, then a **Selfie** step — “Tap to capture or upload”.

**API:** `POST /kyc/submit` accepts exactly:

```json
{ "id_document_no": "108273948", "id_document_type": "NATIONAL_ID" }
```

**There is no file upload endpoint in the entire API** — no multipart handler, no
object storage, no selfie/liveness field. The two Verify Account screens cannot
be implemented.

This also conflicts with §4.4: `id_document_no` is **required at registration**,
yet the design defers identity capture to a separate post-signup KYC flow. Both
cannot be true.

**Required backend work:** a multipart upload endpoint, encrypted object storage
with signed retrieval for admin review, a selfie/liveness field, and
`id_document_no` relaxed to optional at registration. This is the **largest
single gap** between design and backend.

### 4.6 ⚠️ PIN setup needs the password; the design does not ask for it

**Screen:** PIN confirmation page

`POST /auth/set-pin` requires `{ "pin": "...", "password": "..." }`.

During onboarding this is fine — the client still holds the password from
registration. But **User Profile → “Change Security PIN”** hits the same endpoint
from a session where the password is not in memory, so that flow **must prompt
for the password first**. Add that step to the design.

The API accepts **4–6 digits** (`^\d{4,6}$`); the design shows four boxes. Four is
valid — just be aware the backend permits longer.

### 4.7 ⚠️ Receipts show a recipient name the API does not return

**Screens:** Transfer Successful, Receipt Page, Main – Modal Confirmation

Designs show **“Recipient: Jean Paul Mbarga”** and **“Sent To: Jean Doe · +237 671
234 567”**.

`TransactionReceiptResponse` returns `user_name` and `user_phone` — these are the
**wallet owner**, not the counterparty. There is no name lookup for a destination
MSISDN, and no telco name-resolution call.

Show the **destination phone number** only, or add a confirmed-recipient-name
capability to the backend. Do not display `user_name` in the “Sent To” slot — on a
withdrawal that would show the sender's own name as the recipient.

Field mapping that *does* work:

| Design label | API field |
| --- | --- |
| Amount Sent | `amount` |
| Fee | `fee` |
| Date & Time | `completed_at` (fall back to `created_at`) |
| Transaction ID | `transaction_id` |
| Payment Channel | `channel` |
| Ref No. | `external_ref` |
| Sent From (wallet) | `user_name` / `user_phone` |

### 4.8 ⚠️ “Download PDF” and “Share Receipt” are client-side

**Screens:** Receipt Page, Main – Modal Confirmation

No endpoint returns a PDF. `GET /wallet/transactions/{id}` returns JSON. Render
the PDF on-device (e.g. Dart `pdf` + `printing`) from the receipt payload, and
wire “Share Receipt” to the platform share sheet. No backend work needed — just
don't plan for a server-rendered document.

### 4.9 ⚠️ Deposit fee direction differs from withdrawal

**Screen:** Amount Deposit Page — “Processing fee 100 FCFA”, “Total Deducted 0 FCFA”

The two flows charge fees in **opposite directions**:

| | Charged to source | Credited to wallet |
| --- | --- | --- |
| **Deposit** (0.5%) | `amount` | `amount − fee` |
| **Withdraw** (1.0%) | `amount + fee` | — |

Verified: `POST /public/fee-calculator` with `{"type":"DEPOSIT","channel":"MTN","amount":25000}`
→ `fee: 125.00`, `total_charged: 25000.0`, **`net_credited: 24875.00`**.

So on the deposit screen **“Total Deducted” equals the amount**, and the user
needs a separate **“You'll receive: 24,875 FCFA”** line. The current label implies
the fee is added on top, which is the withdrawal model. Reversing this in the UI
means every depositing user is surprised by a smaller balance than expected.

The Withdrawal Page is correct as drawn: 50,000 FCFA at 1% → “Estimated Fee −500
FCFA”, total debit 50,500. ✅

Always call `/public/fee-calculator` for the displayed figure rather than
computing it client-side — the percentages are server configuration and will
change.

### 4.10 ⚠️ User Profile shows unsupported items

**Screen:** User Profile

| Design element | Backend |
| --- | --- |
| Profile photo | No avatar field on `UserResponse` |
| **“My QR Code”** | No endpoint |
| **“Upgrade Account”** | No account-tier concept |
| **“Payment Methods”** | Closest is `GET /wallet/linked-accounts` — rename or repoint |
| KYC Status **“Approved”** | API value is **`VERIFIED`** — map the label |
| “John Doe ✓ Verified”, phone | ✅ `GET /auth/me` |

The QR code could be generated client-side from the user's phone number without
backend work, if the intent is “show my number to be paid” — though being *paid*
requires the P2P gap in §4.2 to be closed first.

---

## 5. Public Endpoints (no auth)

### `GET /api/v1/public/health`
Liveness. `data.status == "UP"`. No database I/O (p95 ≈ 0.43 ms) — safe to poll.
Optionally used by **Splash Welcome** for a reachability check.

### `GET /api/v1/public/info`
Drives the **Dashboard → Network Status** panel and the **Deposit Page** provider
list:

```json
{ "active_channels": { "MTN_MOMO": "ACTIVE", "ORANGE_MONEY": "ACTIVE", "UBA_BANK": "PLANNED_V2" } }
```

Render the “MTN Mobile Money · Online / Orange Money · Online” rows from this, not
from hard-coded values — UBA flips to active in Phase 2 with no client release.

### `POST /api/v1/public/fee-calculator`
Powers the landing-page simulator, the **Withdrawal Page** fee line, and the
**Amount Deposit Page** totals.

```json
{ "type": "DEPOSIT", "channel": "MTN", "amount": 25000.00 }
```

All three fields **required** — omitting `channel` is a 422. See §4.9 for how to
label the result.

---

## 6. Flutter — Screen by Screen

### Onboarding Page → Splash Welcome
Static. “Fast & Secure Transfers · Send money to MTN and Orange Money instantly
across Cameroon.” Splash offers **Create Account** → Registration Page, and **Log
In** → Login.

### Registration Page → PIN confirmation page → Account Success page

`POST /api/v1/auth/register`

```json
{ "full_name": "Jean-Luc Kamdem", "phone_number": "677889900",
  "email": "jeanluc@example.cm", "password": "SecretP@ssword123",
  "id_document_no": "108273948", "id_document_type": "NATIONAL_ID" }
```

- `phone_number` is normalised server-side — `677889900`, `+237677889900` and
  `237677889900` resolve to one account. Display the value the server returns.
- `password` min 8 characters. `email` optional.
- `id_document_no` is **encrypted at rest (AES-256-GCM)** and only ever returned
  masked.
- Accumulate all three wizard steps and submit **once**. See §4.4 (referral code)
  and §4.5 (ID capture ordering).

> **Privilege fields are ignored.** Sending `role` or `kyc_status` has no effect;
> new users are always `CUSTOMER` / `PENDING`. Verified by test.

Then **PIN confirmation page** → `POST /auth/set-pin` (§4.6), then **Account
Success page** → Dashboard.

### Login

`POST /api/v1/auth/login` → `{ "phone_number": "...", "password": "..." }`

```json
{ "access_token": "...", "refresh_token": "...", "expires_in": 1800,
  "user_id": "...", "role": "CUSTOMER", "has_pin": false, "kyc_status": "PENDING" }
```

`has_pin` and `kyc_status` come back on login precisely so you can route
immediately — to PIN setup or to Verify Account — without an extra round trip.

Access tokens last 30 minutes. On 401 `AUTHENTICATION_FAILED`, call
`POST /auth/refresh` **once**; if that fails, clear credentials and return here.

Store tokens in `flutter_secure_storage`, never `SharedPreferences`.

**See §4.1 — the PIN-based login on this screen is not supported by the API.**

### Dashboard

| Element | Call |
| --- | --- |
| Total Balance `FCFA 1,450,000` | `GET /wallet/balance` → `data.balance` |
| Recent Activity (3 rows) | `GET /wallet/transactions?page=1&page_size=3` |
| Network Status | `GET /public/info` → `active_channels` |
| Deposit / Withdraw / History | Navigation only |
| **Send** | **No endpoint — §4.2** |

> **Surface `locked_balance` as “pending”.** Funds held for an in-flight
> withdrawal sit there. If you show only `balance`, a customer mid-withdrawal
> sees money that appears to have vanished.

### Deposit Page → Amount Deposit Page

Provider picker (MTN Mobile Money / Orange Money) driven by `/public/info`, then:

`POST /api/v1/wallet/deposit`

```json
{ "channel": "MTN", "amount": 40000.00, "phone_number": "+237677001122",
  "idempotency_key": "<uuid-v4>" }
```

Returns **202 Accepted** with `transaction_id`, `external_ref`, `status`.

**202 does not mean settled:**

```
POST /wallet/deposit ──► 202, status=PENDING|PROCESSING
                          │
        customer approves on their MoMo/OM handset
                          │
   provider ──► POST /api/v1/webhooks/{mtn|orange} ──► SUCCESS, wallet credited
```

Show a “confirm on your phone” state, then poll
`GET /wallet/transactions/{transaction_id}` every 2–3 s with backoff, ~2 min cap,
until `status` leaves `PENDING`/`PROCESSING`. **Never optimistically credit the
UI.** On success → Transfer Successful; on failure show the `failure_reason`.

Quick-amount chips (+1,000 / +5,000 / +10,000) are client-side. See §4.9 for the
fee labels.

### Withdrawal Page

`POST /api/v1/wallet/withdraw`

```json
{ "channel": "ORANGE", "amount": 30000.00, "destination_phone": "+237699887766",
  "pin": "4417", "idempotency_key": "<uuid-v4>" }
```

`pin` **required**. Returns 202; same webhook-settled lifecycle as deposit.

Funds move to `locked_balance` at initiation and are debited on settlement. If the
telco fails or times out, **the hold is released automatically and the balance
returns** — verified under sustained outage and concurrency in Sprint 4. So a
failed withdrawal briefly shows a reduced available balance before it restores;
poll to a final state before telling the user anything definitive.

Total debited is `amount + fee` (1.0%, min 25 XAF) — show the total, not just the
amount.

> **Five consecutive wrong PINs freeze the wallet** and dispatch a security alert.
> `detail` carries “Attempt N of 5” — surface the remaining count so the freeze is
> never a surprise. After freezing, calls return `WALLET_FROZEN` and only an admin
> can restore access.

### Transfer Successful → Receipt Page / Main – Modal Confirmation

All three render `GET /api/v1/wallet/transactions/{transaction_id}`. Field mapping
and the recipient-name problem are in §4.7; PDF/share in §4.8.

### Transaction History

`GET /api/v1/wallet/transactions?page=1&page_size=20&tx_type=&channel=&tx_status=`

Note the parameter names: **`page_size`** (not `size`), **`tx_type`** and
**`tx_status`** (not `type` / `status`).

The **All / Deposits / Withdrawals** filter maps to `tx_type=DEPOSIT|WITHDRAW`. ✅

Statuses: `PENDING` → `PROCESSING` → `SUCCESS` | `FAILED`, plus **`REVERSED`** for
admin reversals. Render `REVERSED` distinctly — the balance moved back, and an
unexplained reversal generates support load. The design's status chips cover
Pending; add a Reversed treatment.

See §4.2 re. the “Bank Transfer” row and merchant descriptions.

### Notifications Feed

`GET /api/v1/notifications?page=1&page_size=20&channel=`

**Blocked as designed — see §4.3.** Buildable today: a flat list of `message` +
`created_at`. Not buildable: category tabs, unread badges, mark-all-as-read,
“Review actions”.

### User Profile → Verify Account 1 → Verify Account 2

Profile from `GET /auth/me`; KYC state from `GET /kyc/status` (returns
`kyc_status`, `id_document_masked`, `verified_at`).

Statuses `PENDING` → `VERIFIED` | `REJECTED` (with `rejection_reason`). Map
`VERIFIED` to the design's **“Approved”** chip. Transacting while unverified
returns `KYC_REQUIRED`.

The API only ever returns a **masked** document number — show the mask; plaintext
is not retrievable through the API by design.

**Verify Account 1 and 2 are blocked — see §4.5.**

Unsupported profile items: §4.10.

### Linked accounts

- `GET /api/v1/wallet/linked-accounts`
- `POST /api/v1/wallet/linked-accounts` — `{ "provider": "MTN", "account_identifier": "+237677001122", "is_default": true }`

Also auto-created on first use of a number in a deposit/withdrawal. This is the
closest match for the profile's **“Payment Methods”** row.

---

## 7. Angular Admin Back-Office

No Figma frames were supplied for the admin portal; these are contract notes.

All endpoints require `ADMIN` (reconciliation also allows `AUDITOR`). A `CUSTOMER`
token gets `PERMISSION_DENIED`; no token gets 401. Both enforced and tested.

### Users and KYC review
- `GET /api/v1/admin/users?page=&page_size=&search=&role=`
- `POST /api/v1/admin/users/{user_id}/status?status_val=&reason=` — **query parameters, not a body**
- `POST /api/v1/kyc/review/{user_id}` — `{ "status": "VERIFIED" | "REJECTED", "rejection_reason": "..." }`

> The review queue can only show the **masked** document number and type — there
> are no uploaded images to review (§4.5). Reviewers currently have nothing to
> visually verify against.

### Wallet controls
- `POST /api/v1/admin/wallets/{wallet_id}/status` — `{ "status": "FROZEN", "reason": "..." }`
- `PUT /api/v1/admin/wallets/{wallet_id}/limits` — `{ "daily_limit": 500000.00, "monthly_limit": 5000000.00 }`

Also the recovery path for a PIN lockout.

### Transactions and reversals
- `GET /api/v1/admin/transactions?page=&page_size=&tx_type=&channel=&status=&search=`
- `POST /api/v1/admin/transactions/{transaction_id}/reverse` — `{ "reason": "...", "admin_notes": "..." }`

> **Reversal is irreversible and single-shot.** Only `SUCCESS` transactions can be
> reversed; a second attempt is refused with `INVALID_STATE_TRANSITION` and does
> not double-debit (tested). Reversing a deposit whose funds were since spent
> fails with `INSUFFICIENT_FUNDS_FOR_REVERSAL`. Require typed confirmation and
> always capture the reason — it writes to the immutable audit log.

### Settlement reconciliation

`POST /api/v1/admin/reconcile`

```json
{ "channel": "MTN", "start_date": "2026-09-07", "end_date": "2026-09-07",
  "partner_records": [
    { "external_ref": "MTN-MOMO-5002663C", "amount": 5000.00,
      "currency": "XAF", "status": "SUCCESS", "channel": "MTN" }
  ] }
```

Every record requires `external_ref`, `amount`, `status` **and `channel`** —
omitting `channel` is a 422.

Mismatch types: `AMOUNT_MISMATCH`, `STATUS_MISMATCH`, `MISSING_IN_INTERNAL`,
`MISSING_IN_PARTNER`.

> `MISSING_IN_PARTNER` means PayDay settled something the telco never reported.
> **Do not offer a one-click reverse here** — it needs provider confirmation
> first. Link to the transaction instead.

### Audit log
`GET /api/v1/admin/audit-logs?page=&page_size=&action=&entity_name=` — append-only,
read-only in the UI by design.

---

## 8. Generated SDKs

Do not hand-write HTTP clients. `.github/workflows/generate-client-sdks.yml`
produces both on every API change:

| Client | Generator | Artefact |
| --- | --- | --- |
| Flutter | `dart-dio` | `payday-sdk-dart` |
| Angular ×2 | `typescript-angular` | `payday-sdk-typescript` |

The contract is guarded: removing an operation, or adding a newly-required field
to an existing request schema, **fails CI** (`test_sprint4_contract_drift.py`).
Both are breaking for shipped mobile clients that cannot be rolled back.

---

## 9. Local Development

```bash
uvicorn payday.main:app --host 0.0.0.0 --port 8000   # or: docker compose up --build
```

Swagger UI `/docs` · ReDoc `/redoc` · Spec `/openapi.json`

### CORS
Explicit allowlist, not `*`. Allowed by default: `http://localhost:4200`,
`http://127.0.0.1:4200`, `http://localhost:3000`, `http://localhost:8000`.
Add other ports to `BACKEND_CORS_ORIGINS` in `.env`. **Do not set `["*"]`** —
combined with credentials that reflects any origin and was a real vulnerability
fixed in Sprint 4. CORS does not affect Flutter; it is browser-enforced only.

### Simulating telco callbacks

Without provider credentials, drive settlement yourself so the deposit/withdrawal
screens reach a final state:

```bash
curl -X POST http://localhost:8000/api/v1/webhooks/mtn \
  -H 'Content-Type: application/json' \
  -d '{"transaction_id":"<id>","external_ref":"<ref>","status":"SUCCESSFUL"}'
```

`/api/v1/mock-telco/{mtn|orange}/simulate-callback` does the same with
provider-shaped payloads. Both are sandbox aids — real webhooks are HMAC-SHA256
verified with anti-replay protection.

---

## 10. Integration Checklist

**Contract hygiene**
- [ ] Unwrap the `data` envelope once, centrally
- [ ] Branch on `code`, never `detail`
- [ ] `Decimal` for all money — never `double` / JS `number`
- [ ] One currency label app-wide (§1)
- [ ] Use the generated SDKs

**Money movement**
- [ ] Send an `idempotency_key` on every deposit and withdrawal
- [ ] Treat 202 as *initiated*; poll to a final status
- [ ] Show `locked_balance` as pending, never hide it
- [ ] Call `/public/fee-calculator` rather than computing fees client-side
- [ ] Deposit shows “You'll receive” = `net_credited` (§4.9)
- [ ] Render `REVERSED` transactions distinctly

**Auth & identity**
- [ ] Resolve the PIN-login question (§4.1)
- [ ] Surface remaining PIN attempts before the 5-attempt freeze
- [ ] Refresh on 401 exactly once, then log out
- [ ] Tokens in secure storage
- [ ] Prompt for password on “Change Security PIN” (§4.6)

**Blocked pending backend decisions**
- [ ] §4.1 PIN login + password reset
- [ ] §4.2 P2P “Send”, bill payments, bank channel
- [ ] §4.3 Notification categories and read state
- [ ] §4.4 Referral code field
- [ ] §4.5 KYC document/selfie upload
- [ ] §4.7 Recipient name resolution
