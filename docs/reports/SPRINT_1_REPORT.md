# Sprint 1 Completion Report & Technical Handover

**Document Title:** PayDay Sprint 1 Executive & Developer Handover Report  
**Sprint Name:** Sprint 1 — Foundations, Authentication, KYC & Core Ledger Engine  
**Release Target:** PayDay Version 1.0 (Republic of Cameroon)  
**Date:** September 2026  
**Author / Roles:** System Architect, Computer Engineer, Backend Developer  
**Recipients:** Project Manager, Frontend Developer (Flutter & Angular Teams)  
**Status:** **100% COMPLETED & VERIFIED** (24/24 Automated Tests Passing)  

---

# SECTION A: Executive Report for the Project Manager

## 1. Executive Summary
Sprint 1 of the PayDay e-Wallet project has been completed on schedule with all architectural, security, database, and business logic requirements fulfilled. The Python backend (FastAPI + PostgreSQL + SQLAlchemy 2.0 Async + Redis) is operational, stable, and tested for high-concurrency resilience.

The system is currently running live on **port 8000**, serving the interactive Swagger documentation (`/docs`) and ReDoc (`/redoc`) contracts for immediate consumption by the frontend engineering team.

---

## 2. Sprint 1 Deliverables & Milestone Matrix

| Feature / Module | Scope Target | Status | Engineering Artifacts |
| :--- | :--- | :---: | :--- |
| **System Scaffolding** | Clean Hexagonal Architecture, Settings management, Structured logging | **DONE** | `src/payday/core/`, `src/payday/main.py` |
| **Database Schema** | Relational data models (`users`, `wallets`, `linked_accounts`, `transactions`, `notifications`, `audit_logs`) | **DONE** | `src/payday/models/`, `alembic/versions/` |
| **Schema Migrations** | Reversible, version-controlled database migrations with Alembic | **DONE** | `alembic.ini`, `alembic/env.py` |
| **Authentication & RBAC** | Password hashing (Bcrypt), JWT Access/Refresh tokens, Multi-role RBAC (`CUSTOMER`, `ADMIN`, `AUDITOR`) | **DONE** | `src/payday/services/auth_service.py` |
| **Transaction PIN** | 4–6 digit numerical PIN hashing (Bcrypt) and secure validation | **DONE** | `src/payday/core/security.py` |
| **KYC & PII Protection** | AES-256-GCM binary encryption for National ID/Passport numbers at rest | **DONE** | `src/payday/core/encryption.py`, `kyc_service.py` |
| **Atomic Wallet Engine** | Pessimistic row locking (`SELECT FOR UPDATE`), funds hold/reservation, compensatory rollbacks | **DONE** | `src/payday/services/wallet_engine.py` |
| **Multi-Client API** | 19 REST endpoints supporting Flutter Mobile, Angular Landing, and Angular Admin | **DONE** | `src/payday/api/v1/` |

---

## 3. ACID Compliance & Concurrency Audit Results

To ensure zero fund loss or ledger corruption, the backend was subjected to a battery of 24 automated unit, integration, and concurrency stress tests. All 24 tests passed without failure.

```
============================= 24 passed in 11.26s ==============================
```

### Key Stress Test Highlights:
1. **Parallel Withdrawal Attack (`test_parallel_withdrawal_attack`):**
   - *Test Scenario:* 50 simultaneous parallel withdrawal coroutines fired against a wallet containing only $10,000\text{ XAF}$.
   - *Result:* **Exactly 1** transaction succeeded, and **49 were safely rejected** with `InsufficientFundsError`.
   - *Significance:* Eliminates the risk of double-spend exploits during rapid app taps or delayed telco webhook retries.
2. **Ledger Invariant Invariance (`test_ledger_invariant_verification`):**
   - *Formula:* $\text{Wallet.balance} = \sum(\text{SUCCESS Deposits}) - \sum(\text{SUCCESS Withdrawals}) - \sum(\text{Fees})$.
   - *Result:* Verified on every balance transition; database check constraints (`chk_wallet_positive_balance`) permanently prevent negative balances.
3. **Database Migration Reversibility (`test_alembic_forward_and_backward_migrations`):**
   - *Cycle:* `alembic upgrade head` $\rightarrow$ `alembic downgrade base` $\rightarrow$ `alembic upgrade head`.
   - *Result:* Clean creation, complete teardown, and clean restoration with zero orphaned foreign keys or schema drift.

---

## 4. Risk & Readiness Assessment for Sprint 2

| Risk Factor | Severity | Mitigation Strategy Implemented in Sprint 1 |
| :--- | :---: | :--- |
| **Telco Webhook Delays** | Medium | Built-in withdrawal hold mechanism (`locked_balance`) isolates funds while external channel resolves. |
| **Regulatory (COBAC) PII Audits** | High | Customer National IDs and sensitive KYC identifiers are encrypted using AES-256-GCM at rest. |
| **Simultaneous UI Clicks** | High | Pessimistic row-level locking + unique `idempotency_key` constraint. |
| **Telco Sandbox Maintenance** | Medium | Built-in offline mock simulation adapters ready for Sprint 2 development. |

**Sprint 2 Readiness Verdict:** **GREEN (Ready to Proceed)**  
*Next Phase:* Transaction Manager state machine, MTN Mobile Money Collection (`RequestToPay`) & Disbursement (`Transfer`), and Webhook handlers.

---
---

# SECTION B: Technical Integration Guide for Frontend Developers

**Target Clients:**
1. **Flutter Mobile App:** End-Customer e-Wallet (Android & iOS).
2. **Angular Landing Page & Simulator:** Public marketing website & fee demo.
3. **Angular Admin Portal:** Operations, KYC manual review, wallet freezing, and ledger audit.

---

## 1. Connection & Environment Details

| Parameter | Development Value | Production / Staging |
| :--- | :--- | :--- |
| **Base URL** | `http://localhost:8000/api/v1` (or live preview URL) | `https://api.payday.cm/api/v1` |
| **Swagger UI** | `http://localhost:8000/docs` | `https://api.payday.cm/docs` |
| **ReDoc** | `http://localhost:8000/redoc` | `https://api.payday.cm/redoc` |
| **OpenAPI Schema** | `http://localhost:8000/openapi.json` | `https://api.payday.cm/openapi.json` |
| **CORS Policy** | Allowed for all origins (`http://localhost:4200`, mobile apps) | Strict domain allowlist |

---

## 2. Automated Code Generation Pipeline

You do not need to write boilerplate DTO models or HTTP request services by hand. You can generate them directly from our backend OpenAPI schema:

### A. Flutter (Dart) Client Generation
Generate strongly-typed Dart models and API clients using `swagger_parser` or `openapi-generator-cli`:
```bash
# Using openapi-generator-cli for Dart (Dio / HTTP)
npx @openapitools/openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g dart-dio \
  -o ./lib/api/
```

### B. Angular (TypeScript) Client Generation
Generate TypeScript models and injectable Angular services using `@openapitools/openapi-generator-cli`:
```bash
# Generate Angular HttpClient services
npx @openapitools/openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g typescript-angular \
  -o ./src/app/core/api/
```

---

## 3. Authentication & Security Flow

### Token Lifecycle
1. **Login:** Send `POST /api/v1/auth/login` $\rightarrow$ Receive `access_token` (expires in 30 mins) and `refresh_token` (expires in 7 days).
2. **Header Attachment:** For all authenticated endpoints, attach the access token:
   ```http
   Authorization: Bearer <access_token>
   ```
3. **Token Refresh:** When an API call returns `401 Unauthorized`, call `POST /api/v1/auth/refresh` with `{ "refresh_token": "<token>" }` to get a fresh access token without logging the user out.

### Transaction PIN Setup (Crucial for Mobile App)
- At registration, `has_pin` is returned as `false`.
- The Flutter app should prompt the user to configure a 4 to 6 digit numeric transaction PIN via `POST /api/v1/auth/set-pin`.
- The PIN is required for money movement operations (Deposit / Withdraw in Sprint 2).

---

## 4. Input Formatting: Cameroon Phone Numbers (MSISDN)

The backend accepts standard Cameroon phone formats and normalizes them automatically to international E.164 (`+2376XXXXXXXX`):
- `677112233` $\rightarrow$ `+237677112233`
- `237699445566` $\rightarrow$ `+237699445566`
- `+237655001122` $\rightarrow$ `+237655001122`

---

## 5. Standardized Error Handling (RFC 7807)

All non-2xx responses adhere strictly to the RFC 7807 `ProblemDetail` specification. Use the `code` field to trigger user-friendly UI banners or error dialogs:

```json
{
  "type": "https://payday.cm/errors/insufficient-funds",
  "title": "Insufficient Funds",
  "status": 400,
  "detail": "Insufficient wallet balance. Available: 5000.00 XAF, Required (with fees): 10100.00 XAF.",
  "instance": "/api/v1/wallet/withdraw",
  "code": "INSUFFICIENT_FUNDS",
  "extra": {
    "available_balance": 5000.0,
    "required_amount": 10100.0
  },
  "timestamp": "2026-09-01T12:00:00Z"
}
```

### Standard Error Codes:
- `AUTHENTICATION_FAILED`: Invalid phone number/password or expired token.
- `PERMISSION_DENIED`: Insufficient RBAC role (e.g. non-admin accessing admin portal).
- `USER_ALREADY_EXISTS`: Phone number or email already registered.
- `INSUFFICIENT_FUNDS`: Available balance less than requested amount + fee.
- `DAILY_LIMIT_EXCEEDED`: Transaction exceeds user daily ceiling.
- `WALLET_FROZEN`: Account is suspended/frozen by compliance.
- `INVALID_PIN`: Incorrect transaction PIN entered.
- `PIN_NOT_SET`: User attempted money movement without setting a PIN.
- `KYC_REQUIRED`: Action requires verified KYC status.

---

## 6. Primary Endpoint Catalog & Payload Examples

### 1. User Registration & Auto Wallet Creation
- **Endpoint:** `POST /api/v1/auth/register`
- **Request Body:**
  ```json
  {
    "full_name": "Jean-Luc Kamdem",
    "phone_number": "+237699112233",
    "email": "jeanluc@example.cm",
    "password": "Password123!",
    "id_document_no": "109283746",
    "id_document_type": "NATIONAL_ID"
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "success": true,
    "message": "User registered successfully. Central XAF wallet generated.",
    "data": {
      "user_id": "8b51d388-3486-4fba-bb6a-55447a13d719",
      "full_name": "Jean-Luc Kamdem",
      "phone_number": "+237699112233",
      "wallet_id": "a90e3860-e4df-4475-b6ec-7dcf202722b5",
      "currency": "XAF",
      "balance": "0.00",
      "kyc_status": "PENDING"
    }
  }
  ```

---

### 2. User Login
- **Endpoint:** `POST /api/v1/auth/login`
- **Request Body:**
  ```json
  {
    "phone_number": "699112233",
    "password": "Password123!"
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "data": {
      "access_token": "eyJhbGciOiJIUzI1Ni...",
      "refresh_token": "eyJhbGciOiJIUzI1Ni...",
      "token_type": "bearer",
      "expires_in": 1800,
      "user_id": "8b51d388-3486-4fba-bb6a-55447a13d719",
      "role": "CUSTOMER",
      "has_pin": false,
      "kyc_status": "PENDING"
    }
  }
  ```

---

### 3. Set Transaction PIN
- **Endpoint:** `POST /api/v1/auth/set-pin`
- **Headers:** `Authorization: Bearer <access_token>`
- **Request Body:**
  ```json
  {
    "pin": "1234",
    "password": "Password123!"
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "message": "Transaction PIN configured successfully",
    "data": { "user_id": "8b51d388-3486-4fba-bb6a-55447a13d719", "has_pin": true }
  }
  ```

---

### 4. Fetch Wallet Balance
- **Endpoint:** `GET /api/v1/wallet/balance`
- **Headers:** `Authorization: Bearer <access_token>`
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "data": {
      "wallet_id": "a90e3860-e4df-4475-b6ec-7dcf202722b5",
      "balance": 50000.0,
      "locked_balance": 0.0,
      "available_balance": 50000.0,
      "currency": "XAF",
      "status": "ACTIVE"
    }
  }
  ```

---

### 5. Link External Account (MTN / Orange)
- **Endpoint:** `POST /api/v1/wallet/linked-accounts`
- **Headers:** `Authorization: Bearer <access_token>`
- **Request Body:**
  ```json
  {
    "provider": "MTN",
    "account_identifier": "677112233",
    "is_default": true
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "success": true,
    "message": "MTN account linked successfully",
    "data": {
      "linked_account_id": "3bca2d89-4089-4b2a-a92c-5bcfd6582bf2",
      "user_id": "8b51d388-3486-4fba-bb6a-55447a13d719",
      "provider": "MTN",
      "account_identifier": "+237677112233",
      "is_verified": true,
      "is_default": true,
      "created_at": "2026-09-01T12:30:00Z"
    }
  }
  ```

---

### 6. Public Fee Calculator (for Angular Landing Demo)
- **Endpoint:** `POST /api/v1/public/fee-calculator`
- **Request Body:**
  ```json
  {
    "type": "WITHDRAW",
    "channel": "ORANGE",
    "amount": 25000.00
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "data": {
      "amount": 25000.0,
      "fee": 250.0,
      "total_charged": 25250.0,
      "net_credited": 25000.0,
      "currency": "XAF",
      "fee_percentage": 1.0
    }
  }
  ```

---

### 7. Admin: User Directory & Status Controls (Angular Admin Portal)
- **List Users:** `GET /api/v1/admin/users?page=1&page_size=20&search=kamdem`
- **Suspend/Activate User:** `POST /api/v1/admin/users/{user_id}/status?status_val=SUSPENDED&reason=ComplianceReview`
- **Freeze/Unfreeze Wallet:** `POST /api/v1/admin/wallets/{wallet_id}/status` with `{ "status": "FROZEN", "reason": "Risk hold" }`
- **Update Limits:** `PUT /api/v1/admin/wallets/{wallet_id}/limits` with `{ "daily_limit": 1000000.00, "monthly_limit": 10000000.00 }`

---

## 7. Next Steps for Frontend Teams
1. **Flutter Mobile Developer:**
   - Run OpenAPI code generation to import models.
   - Implement Registration, Login, and PIN Setup views.
   - Integrate the Balance and Linked Accounts views.
2. **Angular Web Developer (Landing Page):**
   - Connect the public interactive Fee Calculator (`POST /api/v1/public/fee-calculator`).
   - Connect the Live System Status badge (`GET /api/v1/public/info`).
3. **Angular Admin Developer:**
   - Wire up Admin Login with JWT auth interceptors.
   - Build the User Management and Wallet Freeze/Limit Modification dashboard views.
