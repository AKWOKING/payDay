# PayDay — System Architecture, Engineering Specification & SDLC Roadmap

**Document Version:** 1.1.0  
**Project:** PayDay — Integrated e-Wallet for Cameroon (MTN MoMo, Orange Money & UBA)  
**Author / Roles:** System Architect, Computer Engineer, Backend Developer  
**Status:** Approved for Implementation  
**Backend Preferred Stack:** Python 3.11+ / FastAPI / PostgreSQL / SQLAlchemy 2.0 Async / Redis / Celery  
**Frontend Clients & Tooling:**
- **Mobile Client:** Flutter (Flutter SDK, Dart, Android/iOS) — Customer Mobile App
- **Web Landing Page & App Demo:** Angular (Angular CLI, TypeScript, Jasmine & Karma)
- **Admin & Operations Dashboard:** Angular (Angular CLI, TypeScript, Jasmine & Karma)
- **Developer Environment:** VS Code, OpenAPI 3.1 Code Generation (Dart & TypeScript)  

---

## 1. Executive Summary & Problem Formulation

### 1.1 Problem Statement
In Cameroon's digital payments ecosystem, financial activity is fragmented across two dominant Mobile Money operators (**MTN Mobile Money** and **Orange Money**) and traditional banking institutions (**United Bank for Africa - UBA**). Users and SMEs routinely hold accounts across multiple providers. Transferring funds between these silos currently requires:
- Physical cash-out / cash-in cycles through human agents.
- Paying compounding intermediary transaction fees.
- Fragmented USSD menus, disparate mobile apps, and unintegrated ledgers.

### 1.2 The Solution: The Triangle Model
**PayDay** introduces a neutral, unified digital e-wallet operating on the **Triangle Model**:
- **Central Wallet:** A single source of truth for user funds denominated in Central African CFA Franc (**XAF**).
- **Bidirectional Ingress & Egress:** Direct deposit (Cash-In) and withdrawal (Cash-Out) pipelines connecting the central wallet to MTN MoMo, Orange Money, and UBA.
- **Intermediary Hub:** External channels never communicate with one another directly. All value transfer resolves through PayDay's auditable transaction ledger.
- **Scope Version 1 (V1):** App wallet + MTN Mobile Money + Orange Money + Admin Back-Office & Reconciliation. (UBA and P2P wallet-to-wallet transfers are architected for seamless extension in V2).

```
                      ┌─────────────────────────┐
                      │      PayDay Wallet      │
                      │ (Central Ledger in XAF) │
                      └────────────┬────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         │                         │                         │
         ▼                         ▼                         ▼
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│ MTN MoMo API    │       │ Orange Money API│       │  UBA Bank API   │
│ (Collections &  │       │ (Web Payment &  │       │ (Direct Debit & │
│ Disbursements)  │       │  Payout API)    │       │ Disbursements)  │
│ [Version 1]     │       │ [Version 1]     │       │ [Version 2 Ext] │
└─────────────────┘       └─────────────────┘       └─────────────────┘
```

---

## 2. System Architecture & Technical Stack

### 2.1 Architectural Pattern: Clean / Hexagonal (Ports & Adapters)
To ensure isolation, testability, and multi-client flexibility, the backend exposes unified REST APIs consumed by three distinct frontend targets:

```
┌───────────────────────────┐ ┌───────────────────────────┐ ┌───────────────────────────┐
│    Flutter Mobile App     │ │    Angular Web Demo       │ │   Angular Admin Portal    │
│ (Customer e-Wallet on     │ │ (Marketing Landing Page & │ │ (Operations, KYC Review,  │
│  Android / iOS)           │ │  Interactive Simulator)   │ │  Ledger Reconciliation)   │
└─────────────┬─────────────┘ └─────────────┬─────────────┘ └─────────────┬─────────────┘
              │                             │                             │
              └─────────────────────────────┼─────────────────────────────┘
                                            │ HTTPS / REST / JSON (OpenAPI v3.1)
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              API Gateway & Security Layer                              │
│  - TLS 1.3 Termination           - Multi-Origin CORS (Web + Mobile)                    │
│  - JWT Bearer & Refresh Rotation - Role-Based Access Control (Customer vs Admin)      │
│  - Distributed Rate Limiting     - RFC 7807 Standard Problem Details                   │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   Application Core                                     │
│  ┌─────────────────────────┐     ┌──────────────────────────────────────────────────┐  │
│  │      Wallet Engine      │     │               Transaction Manager                │  │
│  │ (ACID Balance Mutator)  │◄───►│ (State Machine, Funds Hold & Compensations)      │  │
│  └─────────────────────────┘     └────────────────────────┬─────────────────────────┘  │
│  ┌─────────────────────────┐                              │                            │
│  │ Authentication & KYC    │                              │                            │
│  └─────────────────────────┘                              │                            │
└───────────────────────────────────────────────────────────┼────────────────────────────┘
                                                            │
                                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                             Channel Adapters (Ports Layer)                             │
│  ┌───────────────────────┐         ┌──────────────────────┐         ┌────────────────┐ │
│  │   MTN MoMo Adapter    │         │ Orange Money Adapter │         │  UBA Adapter   │ │
│  │ (RequestToPay/Transfer│         │ (WebPayment/Payout)  │         │ (Future Stub)  │ │
│  └──────────┬────────────┘         └──────────┬───────────┘         └───────┬────────┘ │
└─────────────┼─────────────────────────────────┼─────────────────────────────┼──────────┘
              ▼                                 ▼                             ▼
      MTN MoMo Gateway                  Orange Money Gateway              UBA API Gateway
```

### 2.2 Technology Stack Selection & Justification

| Layer | Technology | Justification |
| :--- | :--- | :--- |
| **Backend Framework** | **Python 3.11+ / FastAPI** | High-performance asynchronous runtime (`asyncio`), native `Pydantic v2` validation, auto-generated interactive OpenAPI/Swagger docs for the Angular developer, clean dependency injection. |
| **Database & ORM** | **PostgreSQL 15+ & SQLAlchemy 2.0 (Async) + Alembic** | Enterprise-grade ACID compliance, robust row-level locking (`SELECT ... FOR UPDATE`), transactional safety for financial ledgers, migration versioning. |
| **In-Memory Cache & Message Broker** | **Redis 7+** | High-throughput distributed rate limiting, token revocation blacklisting, idempotent request locking, and Celery task brokerage. |
| **Asynchronous Task Queue** | **Celery / ARQ** | Background processing of webhook retries, automated timeout polling, and SMS/push notification dispatching without blocking HTTP request threads. |
| **Security & Cryptography** | **Argon2 / Bcrypt / PyCryptodome (AES-256-GCM)** | State-of-the-art password/PIN hashing, envelope encryption for sensitive KYC data (ID documents, phone numbers) at rest. |
| **API Contract & Testing** | **OpenAPI 3.1, Pytest, Pytest-Asyncio, HTTPX** | Strict schema validation, integration tests with mock telco sandboxes, race condition & concurrency verification. |
| **Frontend Coordination** | **Angular Client Interface** | Standardized JSON payload structures, RFC 7807 error responses, JWT auth interceptors, CORS enabled for browser integration. |

---

## 3. Data Architecture & Ledger Integrity Rules

### 3.1 Database Schema (PostgreSQL)

```sql
-- 1. USERS TABLE
CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name VARCHAR(120) NOT NULL,
    phone_number VARCHAR(20) UNIQUE NOT NULL, -- MSISDN e.g. +2376XXXXXXXX
    email VARCHAR(120) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    pin_hash VARCHAR(255), -- 4 to 6 digit transaction PIN
    id_document_no_encrypted BYTEA NOT NULL, -- Encrypted AES-256
    id_document_type VARCHAR(30) NOT NULL DEFAULT 'NATIONAL_ID', -- NATIONAL_ID, PASSPORT, RESIDENCE_PERMIT
    kyc_status VARCHAR(20) NOT NULL DEFAULT 'PENDING', -- PENDING, VERIFIED, REJECTED
    role VARCHAR(20) NOT NULL DEFAULT 'CUSTOMER', -- CUSTOMER, ADMIN, AUDITOR
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE, SUSPENDED, CLOSED
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. WALLETS TABLE
CREATE TABLE wallets (
    wallet_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    balance NUMERIC(14, 2) NOT NULL DEFAULT 0.00 CHECK (balance >= 0.00),
    locked_balance NUMERIC(14, 2) NOT NULL DEFAULT 0.00 CHECK (locked_balance >= 0.00),
    currency CHAR(3) NOT NULL DEFAULT 'XAF',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE, FROZEN, CLOSED
    daily_limit NUMERIC(14, 2) NOT NULL DEFAULT 500000.00,
    monthly_limit NUMERIC(14, 2) NOT NULL DEFAULT 5000000.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. LINKED EXTERNAL ACCOUNTS
CREATE TABLE linked_external_accounts (
    linked_account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL, -- MTN, ORANGE, UBA
    account_identifier VARCHAR(50) NOT NULL, -- MSISDN or Bank Account Number
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_provider_account UNIQUE (user_id, provider, account_identifier)
);

-- 4. TRANSACTIONS TABLE
CREATE TABLE transactions (
    transaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key VARCHAR(100) UNIQUE NOT NULL,
    wallet_id UUID NOT NULL REFERENCES wallets(wallet_id) ON DELETE RESTRICT,
    linked_account_id UUID NOT NULL REFERENCES linked_external_accounts(linked_account_id) ON DELETE RESTRICT,
    type VARCHAR(20) NOT NULL, -- DEPOSIT, WITHDRAW
    channel VARCHAR(20) NOT NULL, -- MTN, ORANGE, UBA
    amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
    fee NUMERIC(14, 2) NOT NULL DEFAULT 0.00 CHECK (fee >= 0),
    net_amount NUMERIC(14, 2) NOT NULL CHECK (net_amount > 0),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING', -- PENDING, PROCESSING, SUCCESS, FAILED, REVERSED
    external_ref VARCHAR(100), -- Partner transaction identifier
    failure_reason VARCHAR(255),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- 5. NOTIFICATIONS TABLE
CREATE TABLE notifications (
    notification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    transaction_id UUID REFERENCES transactions(transaction_id) ON DELETE SET NULL,
    channel VARCHAR(10) NOT NULL, -- SMS, PUSH
    recipient VARCHAR(100) NOT NULL,
    message VARCHAR(500) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING', -- PENDING, SENT, FAILED
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. AUDIT LOGS TABLE
CREATE TABLE audit_logs (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id UUID REFERENCES users(user_id),
    action VARCHAR(50) NOT NULL,
    entity_name VARCHAR(50) NOT NULL,
    entity_id UUID NOT NULL,
    old_state JSONB,
    new_state JSONB,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 3.2 Strict Ledger & Concurrency Integrity Rules
1. **Zero Direct Edits:** Application code cannot execute an arbitrary `UPDATE wallets SET balance = ...`. Balance changes occur solely via the `WalletEngine` inside an active database transaction referencing a valid `Transaction` record.
2. **Pessimistic Row-Level Locking:** During any balance check or mutation, the row is acquired using `SELECT * FROM wallets WHERE wallet_id = :id FOR UPDATE` to prevent race conditions (double-spend attacks).
3. **Withdrawal Hold & Reservation Mechanism:**
   - When a withdrawal is requested, the system verifies `wallet.balance >= (amount + fee)`.
   - Instead of immediately deducting or leaving funds unlocked, the system moves the funds to `locked_balance` or debits balance into a temporary hold state with transaction status `PENDING` / `PROCESSING`.
   - On partner webhook `SUCCESS`: locked funds are cleared, completed timestamp set.
   - On partner webhook `FAILED` or timeout: held funds are automatically released/reverted via compensatory ledger action, transitioning status to `FAILED`.
4. **Idempotency Guarantee:** Every transaction request must supply a client-generated UUID `Idempotency-Key`. Duplicate keys within a 24-hour window return the cached initial response without re-executing transactions.
5. **Auditable Balance Invariant:** At all times, for any wallet:
   $$\text{Wallet.balance} = \sum(\text{SUCCESS Deposits}) - \sum(\text{SUCCESS Withdrawals}) - \sum(\text{Fees})$$

---

## 4. Transaction State Machine & Sequence Flows

```
               [ User Initiates Deposit / Withdrawal ]
                                  │
                                  ▼
                            ( PENDING )
                    (Funds Held for Withdrawal)
                                  │
                                  ▼
               [ Request Dispatched to Telco API ]
                                  │
                                  ▼
                          ( PROCESSING )
                                  │
            ┌─────────────────────┴─────────────────────┐
            │ Webhook / Poll Success                    │ Webhook Failure / Timeout
            ▼                                           ▼
      ( SUCCESS )                                  ( FAILED )
[Balance Finalized & Receipt Sent]            [Hold Released / Compensated]
            │
            │ (Admin / Dispute Manual Reversal)
            ▼
      ( REVERSED )
[Offsetting Transaction Logged]
```

### 4.1 Sequence: Deposit (Cash-In from MTN MoMo / Orange Money)
1. **Initiation:** User selects provider (`MTN` or `ORANGE`), specifies amount, enters Transaction PIN.
2. **Validation:** Backend verifies KYC status, daily/monthly limit compliance, and PIN hash.
3. **Record Creation:** Transaction created in `PENDING` state with unique `idempotency_key`.
4. **Channel Dispatch:**
   - *MTN MoMo:* Backend calls MoMo `RequestToPay` API (`/collection/v1_0/requesttopay`). Customer receives USSD prompt on their mobile phone to authorize debit.
   - *Orange Money:* Backend calls OM Web Payment / API (`/orange-money-webpay/dev/v1/webpayment`).
5. **Asynchronous Callback (Webhook):**
   - Telco sends signed webhook callback to `/api/v1/webhooks/{provider}`.
   - Backend verifies HMAC/Signature, matches `external_ref` / `transaction_id`.
   - `WalletEngine` acquires wallet lock (`FOR UPDATE`), increments `balance += amount`, transitions transaction to `SUCCESS`.
6. **Notification:** Celery worker dispatches SMS and Push confirmation to user.

### 4.2 Sequence: Withdrawal (Cash-Out to MTN MoMo / Orange Money)
1. **Initiation:** User specifies destination phone number, amount, and PIN.
2. **Funds Hold:** Backend locks wallet, verifies `balance >= amount + fee`, deducts funds from available balance to pending hold, records transaction as `PROCESSING`.
3. **Disbursement Dispatch:**
   - *MTN MoMo:* Backend executes MoMo `Transfer` API (`/disbursement/v1_0/transfer`).
   - *Orange Money:* Backend executes OM Merchant Payout API.
4. **Callback & Finalization:**
   - If telco confirms `SUCCESS`: transaction marked `SUCCESS`, hold converted to finalized ledger debit.
   - If telco returns `FAILED` or times out: `WalletEngine` releases hold, credits back `amount + fee`, marks transaction `FAILED`, logs `failure_reason`.

---

## 5. Channel Adapter Specifications (The Triangle Integrations)

### 5.1 Abstract Adapter Interface (`PaymentChannelAdapter`)
Every integration inherits from a strictly typed Python protocol:

```python
from abc import ABC, abstractmethod
from typing import Optional
from pydantic import BaseModel
from decimal import Decimal

class DepositRequest(BaseModel):
    transaction_id: str
    phone_number: str
    amount: Decimal
    currency: str = "XAF"
    description: str

class WithdrawalRequest(BaseModel):
    transaction_id: str
    destination_phone: str
    amount: Decimal
    currency: str = "XAF"
    description: str

class ChannelResponse(BaseModel):
    success: bool
    channel_ref: Optional[str]
    status: str # PENDING, SUCCESS, FAILED
    raw_response: dict
    error_message: Optional[str] = None

class PaymentChannelAdapter(ABC):
    @abstractmethod
    async def initiate_deposit(self, req: DepositRequest) -> ChannelResponse:
        """Initiate collection from user's mobile money account."""
        pass

    @abstractmethod
    async def initiate_withdrawal(self, req: WithdrawalRequest) -> ChannelResponse:
        """Initiate payout/transfer to user's mobile money account."""
        pass

    @abstractmethod
    async def verify_webhook(self, headers: dict, body: bytes) -> bool:
        """Verify authenticity of webhook payload (HMAC / signature)."""
        pass

    @abstractmethod
    async def query_status(self, channel_ref: str) -> ChannelResponse:
        """Fallback polling for transaction status reconciliation."""
        pass
```

### 5.2 Provider-Specific Integration Mechanics

#### A. MTN Mobile Money (MoMo API)
- **Authentication:** OAuth 2.0 Client Credentials flow using `Ocp-Apim-Subscription-Key`, API User UUID, and API Key. Access tokens cached in Redis with TTL.
- **Deposit Flow:** Calls `POST /collection/v1_0/requesttopay` using `X-Reference-Id` (UUID). Receives HTTP 202 Accepted. MoMo sends USSD push to subscriber. MoMo posts result to callback URL.
- **Withdrawal Flow:** Calls `POST /disbursement/v1_0/transfer` using `X-Reference-Id`.
- **Status Verification:** `GET /collection/v1_0/requesttopay/{referenceId}` and `GET /disbursement/v1_0/transfer/{referenceId}`.

#### B. Orange Money (OM API Cameroon)
- **Authentication:** Basic Auth token generation exchanging `authorization_header` for short-lived Bearer token.
- **Deposit Flow:** Web Payment API / MPOS collection flow. Initiates payment request with `order_id`, `amount`, `return_url`, and `notif_url`.
- **Withdrawal Flow:** Merchant Payout / B2C Transfer API.
- **Webhook & IPN:** Verifies IPN signature and payload status (`SUCCESSFUL`, `FAILED`, `EXPIRED`).

#### C. Local Mocking & Sandbox Engine
To allow full end-to-end testing and frontend Angular integration without live telco credentials or during sandbox maintenance:
- Built-in Mock Sandbox mode for MTN and Orange adapters.
- Configurable response scenarios: instant success, delayed success (simulating USSD pin entry), insufficient funds, network timeout, and webhook delivery simulation.

---

## 6. Multi-Client Integration Contracts (Flutter & Angular)

The Python FastAPI backend serves as the single source of truth for three distinct frontend interfaces:
1. **Flutter Mobile App (Customer Facing):** Registration, Login, Biometrics, KYC Submission, Transaction PIN, Balance Query, Linked Accounts, MTN/Orange Cash-In, MTN/Orange Cash-Out, Real-Time History & PDF Receipt generation.
2. **Angular Web Landing Page & Demo (Public/Marketing):** Public marketing showcase, interactive fee & transfer calculator, step-by-step interactive demo flow, live status checks.
3. **Angular Admin Dashboard (Operations & Compliance):** Secure Admin login (MFA/RBAC), User directory & KYC manual review/approval, Wallet balance and freeze/unfreeze controls, Live transaction feed & search, Channel settlement reconciliation matrix, System audit trail and limit adjustments.

### 6.1 Tooling & Code Generation Pipeline
- **OpenAPI 3.1 Spec Export:** Backend automatically generates `/openapi.json` from Pydantic schemas.
- **Flutter / Dart SDK Integration:** Frontend developer can generate strongly-typed Dart data models and API services via `openapi-generator-cli` or `swagger_parser` using `dio` / `http`.
- **Angular Integration:** Frontend developer generates TypeScript services and interfaces via `@openapitools/openapi-generator-cli` or `ng-openapi-gen`.
- **Testing Alignment:**
  - Angular Web Apps: **Jasmine & Karma** for unit and component integration tests; mocked backend responses using HTTP interceptors.
  - Flutter Mobile: **Flutter Test & Mockito** for widget and unit testing.
  - Python Backend: **Pytest, pytest-asyncio, HTTPX** for API contract testing and concurrency audits.

### 6.2 Authentication & Security Interceptors
- **Base URL:** `/api/v1`
- **Authentication:** Bearer JWT in `Authorization: Bearer <token>` header with separate token claims for `CUSTOMER` vs `ADMIN`.
- **CORS:** Configured for local development (`http://localhost:4200` for Angular, localhost ports for Flutter web/desktop testing) and production domains.
- **Error Response Standard (RFC 7807):**
  ```json
  {
    "type": "https://payday.cm/errors/insufficient-funds",
    "title": "Insufficient Wallet Balance",
    "status": 400,
    "detail": "Wallet balance (5,000 XAF) is less than withdrawal amount + fee (10,100 XAF).",
    "instance": "/api/v1/wallet/withdraw",
    "code": "INSUFFICIENT_FUNDS",
    "timestamp": "2026-08-28T14:30:00Z"
  }
  ```

### 6.2 Primary REST Endpoints Overview

| Category | Method | Endpoint | Description |
| :--- | :--- | :--- | :--- |
| **Auth** | `POST` | `/api/v1/auth/register` | Register new user + auto-create XAF wallet |
| **Auth** | `POST` | `/api/v1/auth/login` | Login with phone/password, returns JWT pair |
| **Auth** | `POST` | `/api/v1/auth/set-pin` | Set or update 4–6 digit transaction PIN |
| **KYC** | `POST` | `/api/v1/kyc/submit` | Upload ID document details & verify |
| **KYC** | `GET` | `/api/v1/kyc/status` | Get current KYC verification level |
| **Wallet** | `GET` | `/api/v1/wallet/balance` | Get available balance, locked balance & limits |
| **Accounts** | `GET` | `/api/v1/accounts/linked` | List linked MTN / Orange / UBA accounts |
| **Accounts** | `POST` | `/api/v1/accounts/link` | Link a new external mobile money number |
| **Transactions**| `POST` | `/api/v1/wallet/deposit` | Initiate deposit (MTN / Orange) |
| **Transactions**| `POST` | `/api/v1/wallet/withdraw` | Initiate withdrawal (MTN / Orange) |
| **Transactions**| `GET` | `/api/v1/wallet/transactions` | Paginated transaction history + filtering |
| **Transactions**| `GET` | `/api/v1/wallet/transactions/{id}`| Detailed transaction receipt & timeline |
| **Webhooks** | `POST` | `/api/v1/webhooks/mtn` | MTN MoMo asynchronous callback handler |
| **Webhooks** | `POST` | `/api/v1/webhooks/orange` | Orange Money IPN callback handler |
| **Admin** | `GET` | `/api/v1/admin/transactions` | Admin view of all transactions & channel logs |
| **Admin** | `POST` | `/api/v1/admin/reconcile` | Run automated ledger reconciliation report |
| **Admin** | `POST` | `/api/v1/admin/users/{id}/status`| Suspend, activate or freeze user wallet |

---

## 7. SDLC Implementation Roadmap (8-Week Sprint Plan)

```
 Sprint 1 (Weeks 1-2): Foundation & Core Engine
 ├── Clean Architecture Project Scaffolding (FastAPI, Alembic, Docker)
 ├── PostgreSQL Database Schema & Migration Scripts
 ├── Authentication & Security (JWT, Argon2/Bcrypt, Role Management)
 ├── KYC Service & PII Encryption (AES-256)
 └── Wallet Engine Core (Pessimistic Locking & ACID Mutation)
         │
         ▼
 Sprint 2 (Weeks 3-4): Transaction Manager & MTN MoMo Integration
 ├── Transaction State Machine & Idempotency Filter
 ├── Internal REST API Endpoints (Balance, History, Receipts)
 ├── MTN MoMo Adapter (OAuth2, RequestToPay, Transfer)
 ├── MTN MoMo Webhook Handler & Signature Verification
 ├── Telco Sandbox Mock Engine for Local & CI Testing
 └── End-to-End Deposit/Withdraw Testing with MTN MoMo
         │
         ▼
 Sprint 3 (Weeks 5-6): Orange Money, Notifications & Admin Back-Office
 ├── Orange Money Adapter (Web Payment & Payouts)
 ├── Orange Money Webhook / IPN Listener
 ├── Notification Service (SMS & Push dispatching via Background Tasks)
 ├── Admin Back-Office API (User management, limit overrides, audit log)
 └── Automated Ledger Reconciliation Engine (Mismatch detection)
         │
         ▼
 Sprint 4 (Weeks 7-8): Concurrency Hardening, Security Review & Pilot
 ├── Concurrency & Double-Spend Stress Testing (Race condition audits)
 ├── Webhook Failure, Retries & Compensatory Rollbacks Testing
 ├── Security Audit (OWASP Top 10, TLS, Secret Management)
 ├── Complete OpenAPI Documentation Handover for Angular Frontend
 └── Pilot Deployment & Live Simulation Report
```

### Detailed Sprint Breakdown

#### Sprint 1: Weeks 1–2 — Foundations & Core Wallet Engine
- **Objective:** Establish the production-ready Python backend foundation, PostgreSQL schema, authentication system, KYC engine, and atomic wallet ledger.
- **Architectural Deliverables:**
  1. Repository structure following Hexagonal architecture: `core/`, `api/`, `models/`, `schemas/`, `services/`, `adapters/`, `tests/`.
  2. Alembic migration scripts creating `users`, `wallets`, `linked_external_accounts`, `transactions`, `notifications`, and `audit_logs`.
  3. User registration, phone number format validation (Cameroon MSISDN `+237...`), password & transaction PIN hashing.
  4. JWT authentication with short-lived access tokens (15 mins) and refresh tokens (7 days).
  5. `WalletEngine` service implementing atomic balance updates with `SELECT ... FOR UPDATE` row locks.
  6. Comprehensive Pytest test suite for wallet creation, KYC validation, and balance locking.

#### Sprint 2: Weeks 3–4 — Transaction Manager & First Channel (MTN MoMo)
- **Objective:** Build the transaction orchestration state machine, client-facing transaction REST APIs, and the complete MTN Mobile Money adapter.
- **Architectural Deliverables:**
  1. `TransactionManager` implementing the state transitions (`PENDING` -> `PROCESSING` -> `SUCCESS` / `FAILED` / `REVERSED`).
  2. Client REST endpoints: `/api/v1/wallet/deposit`, `/api/v1/wallet/withdraw`, `/api/v1/wallet/balance`, `/api/v1/wallet/transactions`.
  3. `PaymentChannelAdapter` protocol and `MTNMoMoAdapter` implementation.
  4. MTN MoMo Webhook receiver at `/api/v1/webhooks/mtn`.
  5. Built-in Sandbox Mock simulator allowing offline simulation of MTN USSD approvals and rejections.
  6. Angular-ready OpenAPI JSON/YAML documentation export.

#### Sprint 3: Weeks 5–6 — Second Channel (Orange Money), Notifications & Admin Back-Office
- **Objective:** Implement Orange Money integration, notification dispatcher, admin back-office controls, and financial reconciliation.
- **Architectural Deliverables:**
  1. `OrangeMoneyAdapter` supporting OM Web Payment collection and merchant payout disbursement.
  2. Orange Money webhook listener at `/api/v1/webhooks/orange`.
  3. Notification engine supporting SMS and Push alert triggers on transaction state change.
  4. Admin Back-Office APIs: list/search users, adjust limits, inspect channel raw logs, trigger manual reversals.
  5. Daily Automated Reconciliation Service: compares internal ledger totals against simulated/actual partner settlement reports.

#### Sprint 4: Weeks 7–8 — Hardening, Concurrency & Pilot Readiness
- **Objective:** Stress-test edge cases, harden security, run penetration and race condition audits, and prepare pilot operations.
- **Architectural Deliverables:**
  1. Concurrency stress tests: multi-threaded simulated users performing rapid concurrent deposits and withdrawals to prove zero double-spend.
  2. Edge case handling: telco timeout recovery, duplicate webhook replay protection, network partition fallback.
  3. Security review: PII encryption at rest, rate-limiting on sensitive endpoints (PIN verification, OTP, login).
  4. Pilot verification report and final documentation package for Angular team integration.

---

## 8. Quality Assurance, Testing & Security Strategy

### 8.1 Testing Matrix
- **Unit Tests:** All Pydantic validation schemas, PIN hashing, KYC logic, and fee calculation logic (>90% branch coverage).
- **Integration Tests:** Database transactions with PostgreSQL, Alembic migration rollbacks, Redis rate limiting.
- **Concurrency & Race Condition Tests:** Simulating 50 concurrent withdrawal attempts against a single wallet balance to verify strict pessimistic lock isolation.
- **Telco Mock Sandbox:** Comprehensive mock adapters for MTN and Orange simulating all status codes (200, 202, 400, 401, 500, timeouts, callback failures).

### 8.2 Security & Regulatory Compliance (COBAC / BEAC)
1. **PII Data Encryption:** National ID numbers and sensitive credentials encrypted using AES-256-GCM before database insertion.
2. **Strict Limit Enforcement:** Daily limit (e.g. 500,000 XAF) and monthly limit (5,000,000 XAF) checked before processing any transaction.
3. **Transaction Signing & PIN:** Every money-moving operation requires verified 4–6 digit PIN verification. Rate limit of 5 failed PIN attempts before wallet auto-lock.
4. **Audit Trail:** Every administrative action and transaction state change records an immutable `audit_logs` entry.

---

## 9. Deliverable Summary & Next Steps

This roadmap provides the definitive engineering blueprint for building PayDay's backend. 

### Immediate Next Implementation Steps:
1. Initialize the Python project structure (`FastAPI`, `PostgreSQL`, `SQLAlchemy`, `Alembic`, `Pydantic`).
2. Implement the core models and database migration scripts.
3. Build the Authentication, KYC, and Wallet Engine layers.
4. Provide the live OpenAPI/Swagger documentation endpoint for the Angular frontend developer.
