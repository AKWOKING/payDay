# Sprint 2 Completion Report & Technical Handover

**Document Title:** PayDay Sprint 2 Executive & Developer Handover Report  
**Sprint Name:** Sprint 2 — Transaction Manager & First Channel Live (MTN Mobile Money)  
**Release Target:** PayDay Version 1.0 (Republic of Cameroon)  
**Date:** September 2026  
**Author / Roles:** System Architect, Computer Engineer, Backend Developer  
**Recipients:** Project Manager, Frontend Developer (Flutter & Angular Teams)  
**Status:** **100% COMPLETED & VERIFIED** (32/32 Automated Tests Passing)  

---

# SECTION A: Executive Report for the Project Manager

## 1. Executive Summary
Sprint 2 has successfully brought **PayDay's first live external channel (MTN Mobile Money)** into operation alongside the core **Transaction Manager** orchestration engine.

The system now supports full end-to-end **Deposits (Cash-In)** via MTN MoMo Collections (`RequestToPay`) and **Withdrawals (Cash-Out)** via MTN MoMo Disbursements (`Transfer`). Every money movement is governed by an auditable state machine, atomic ledger reservations, transaction PIN verification, and idempotency guarantees.

The backend is currently running live on **port 8000** with **32 passing automated test suites** and live OpenAPI contracts at `/docs`.

---

## 2. Sprint 2 Deliverables & Milestone Matrix

| Feature / Module | Target Scope | Status | Engineering Artifacts |
| :--- | :--- | :---: | :--- |
| **Transaction Manager** | State machine (`PENDING` $\rightarrow$ `PROCESSING` $\rightarrow$ `SUCCESS`/`FAILED`/`REVERSED`) | **DONE** | `src/payday/services/transaction_manager.py` |
| **Idempotency Engine** | Unique `idempotency_key` deduplication on financial requests | **DONE** | `src/payday/models/transaction.py`, `transaction_manager.py` |
| **MTN MoMo Adapter** | Collections (`RequestToPay`), Disbursements (`Transfer`), OAuth2 tokens | **DONE** | `src/payday/adapters/mtn_momo.py` |
| **Adapter Factory** | Channel resolver pattern (`MTN`, `ORANGE`, `UBA` abstraction) | **DONE** | `src/payday/adapters/factory.py` |
| **MTN Webhook Receiver** | Asynchronous callback listener at `/api/v1/webhooks/mtn` | **DONE** | `src/payday/api/v1/webhooks.py` |
| **Mock Telco Simulator** | Developer tool to trigger simulated USSD approvals/rejections | **DONE** | `src/payday/api/v1/mock_telco.py` |
| **Deposit API** | Client endpoint `POST /api/v1/wallet/deposit` | **DONE** | `src/payday/api/v1/transactions.py` |
| **Withdrawal API** | Client endpoint `POST /api/v1/wallet/withdraw` (with PIN check & hold) | **DONE** | `src/payday/api/v1/transactions.py` |
| **History & Receipts** | Paginated transaction history and downloadable receipt DTOs | **DONE** | `src/payday/api/v1/transactions.py` |

---

## 3. Milestone & Schedule Tracking

According to our 8-week delivery plan:
- **Milestone (End of Week 4):** *MTN Mobile Money fully working end-to-end (deposit and withdrawal) with transaction history and webhook handling.*
- **Current Status:** **DELIVERED ON SCHEDULE.**

```
============================= 32 passed in 14.72s ==============================
```

All 32 test suites covering deposits, withdrawals, fee calculations, concurrency holds, hold releases on telco failure, idempotency deduplication, and webhook listeners passed with 100% success.

---

## 4. Sprint 3 Planning & Readiness

With MTN Mobile Money operational, the backend is primed for **Sprint 3 (Weeks 5–6: Orange Money Adapter, Notification Engine & Admin Back-Office Reconciliation)**:
1. **Orange Money Adapter:** Web Payment API and Merchant Payout listener.
2. **Notification Engine:** Asynchronous SMS & Push alert dispatcher on transaction state changes.
3. **Admin Reconciliation Matrix:** Settlement report vs ledger audit tool.

**Sprint 3 Readiness Verdict:** **GREEN (Ready to Proceed)**.

---
---

# SECTION B: Technical Integration Guide for Frontend Developers

**Target Clients:** Flutter Mobile App, Angular Landing Demo, Angular Admin Portal

---

## 1. Updated API Endpoint Reference

| Endpoint | Method | Auth Required | Purpose |
| :--- | :---: | :---: | :--- |
| `/api/v1/wallet/deposit` | `POST` | `Bearer JWT` | Initiate Cash-In from MTN MoMo (USSD prompt sent to user). |
| `/api/v1/wallet/withdraw` | `POST` | `Bearer JWT` | Initiate Cash-Out to MTN MoMo (Requires PIN + places funds hold). |
| `/api/v1/wallet/transactions` | `GET` | `Bearer JWT` | Paginated transaction history (Filter by `tx_type`, `channel`, `tx_status`). |
| `/api/v1/wallet/transactions/{id}` | `GET` | `Bearer JWT` | Full detailed receipt and status breakdown. |
| `/api/v1/webhooks/mtn` | `POST` | Public / Telco | Asynchronous MTN MoMo callback listener. |
| `/api/v1/mock-telco/mtn/simulate-callback` | `POST` | Public / Dev | Simulator to trigger USSD approval or rejection during development. |

---

## 2. Step-by-Step Client Flows (Flutter Mobile App)

### Flow 1: Funding the Wallet (Deposit / Cash-In)
1. **User Action:** Customer selects **MTN Mobile Money**, enters amount (e.g., `10,000 XAF`), and confirms their phone number.
2. **Client Call:**
   ```http
   POST /api/v1/wallet/deposit
   Authorization: Bearer <access_token>
   Content-Type: application/json

   {
     "channel": "MTN",
     "amount": 10000.00,
     "phone_number": "+237677112233",
     "idempotency_key": "c56a4180-65aa-42ec-a945-5fd21dec0538"
   }
   ```
3. **Backend Response (202 Accepted):**
   ```json
   {
     "success": true,
     "message": "Deposit initiated. Please authorize the prompt on your mobile phone.",
     "data": {
       "transaction_id": "7bf314ec-df22-4809-b697-3fcf02cb8722",
       "idempotency_key": "c56a4180-65aa-42ec-a945-5fd21dec0538",
       "wallet_id": "a90e3860-e4df-4475-b6ec-7dcf202722b5",
       "type": "DEPOSIT",
       "channel": "MTN",
       "amount": 10000.0,
       "fee": 50.0,
       "net_amount": 9950.0,
       "status": "PROCESSING",
       "external_ref": "MTN-MOMO-7BF314EC",
       "created_at": "2026-09-03T19:45:00Z"
     }
   }
   ```
4. **User Experience:**
   - App displays a waiting modal: *"Please approve the prompt on your phone by entering your MTN MoMo PIN."*
   - Once the callback arrives, wallet balance updates to include the net amount (`9,950 XAF`).

---

### Flow 2: Withdrawing Funds (Withdrawal / Cash-Out)
1. **User Action:** Customer specifies destination MTN number, amount (e.g., `10,000 XAF`), and enters their **4–6 digit PayDay Transaction PIN**.
2. **Client Call:**
   ```http
   POST /api/v1/wallet/withdraw
   Authorization: Bearer <access_token>
   Content-Type: application/json

   {
     "channel": "MTN",
     "amount": 10000.00,
     "destination_phone": "+237677998800",
     "pin": "1234",
     "idempotency_key": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"
   }
   ```
3. **Hold Mechanism:**
   - The backend immediately reserves `amount + fee` (e.g. `10,100 XAF`) in `locked_balance`. Available balance is reduced immediately so the user cannot double-spend.
   - If MTN confirms success $\rightarrow$ funds permanently debited.
   - If MTN fails or times out $\rightarrow$ hold released automatically.

---

### Flow 3: Viewing Transaction History & Receipts
- **Fetch History:** `GET /api/v1/wallet/transactions?page=1&page_size=20&channel=MTN`
- **Fetch Receipt:** `GET /api/v1/wallet/transactions/{transaction_id}`
  ```json
  {
    "success": true,
    "data": {
      "transaction_id": "7bf314ec-df22-4809-b697-3fcf02cb8722",
      "user_name": "Jean-Luc Kamdem",
      "user_phone": "+237699112233",
      "type": "DEPOSIT",
      "channel": "MTN",
      "currency": "XAF",
      "amount": 10000.0,
      "fee": 50.0,
      "total_charged": 10000.0,
      "net_credited": 9950.0,
      "status": "SUCCESS",
      "external_ref": "MTN-MOMO-7BF314EC",
      "message": "Transaction completed successfully."
    }
  }
  ```

---

## 3. Developer Mock Sandbox Simulation Guide

When developing the Flutter or Angular frontend locally without live MTN telco credentials:
1. Initiate a deposit or withdrawal $\rightarrow$ Note the returned `transaction_id` and `external_ref`.
2. To simulate **customer approving the USSD PIN prompt on their handset**, send:
   ```http
   POST /api/v1/mock-telco/mtn/simulate-callback
   Content-Type: application/json

   {
     "transaction_id": "7bf314ec-df22-4809-b697-3fcf02cb8722",
     "external_ref": "MTN-MOMO-7BF314EC",
     "status": "SUCCESSFUL"
   }
   ```
3. To simulate **customer rejecting prompt or insufficient telco balance**, send:
   ```http
   POST /api/v1/mock-telco/mtn/simulate-callback
   Content-Type: application/json

   {
     "transaction_id": "7bf314ec-df22-4809-b697-3fcf02cb8722",
     "external_ref": "MTN-MOMO-7BF314EC",
     "status": "FAILED",
     "reason": "Customer cancelled USSD authorization"
   }
   ```
4. Verify that your UI transitions gracefully between **PENDING**, **PROCESSING**, **SUCCESS**, and **FAILED** states.
