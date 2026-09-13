# Sprint 6 — WS-2 / LB-7: Revocable Sessions

**Date:** 2026-09-13
**Scope:** launch-blocker workstream WS-2 (LB-7), executed after WS-0/WS-3/WS-5
**Suite:** 133 passed, 2 skipped (was 121/2)
**Author:** backend

---

## 1. Why

`AuthService.refresh_tokens` decoded a JWT, looked the user up, and issued a
fresh pair whenever the account was `ACTIVE`. That was the whole check. There
was no denylist, no `jti` tracking, and no token version, so:

- A stolen refresh token was valid for its **full 7 days** and could not be
  revoked short of suspending the account.
- **Logout was client-side only.** The server had no concept of a session
  ending; clearing the token in the app changed nothing server-side.
- **A correct password reset (LB-1) was impossible.** The purpose of a reset is
  to evict whoever compromised the account. Without revocation the attacker
  kept minting tokens after the victim reset, for up to 7 days, while support
  told the user they were safe. That is why LB-7 was a hard prerequisite of
  LB-1 rather than a parallel task.

Severity was red: 7 days of attacker access surviving the exact remedy a user
would reach for.

---

## 2. What was built

### 2.1 The counter

`users.token_version INTEGER NOT NULL DEFAULT 0` (migration
`003_add_token_version`, down_revision `002_fix_schema_drift`).

- `NOT NULL` with a **server-side default** is deliberate: existing rows
  backfill to 0, which keeps tokens minted before the deploy valid until the
  first real revocation instead of logging every user out at once.
- The roadmap planned this as migration 004 because it assumed WS-1
  (notification delivery) would land first. WS-2 ran first, the chain head was
  still `002_fix_schema_drift`, so the revision is **003**. The gap-free chain
  matters more than the planned number.

### 2.2 The claim and the check

- `create_access_token` / `create_refresh_token` now take a **required**
  `token_version` argument and embed it as `tv`. Required rather than defaulted
  so a new issuance site cannot silently mint an unrevocable token; the type
  error is louder than the runtime symptom would be.
- `token_version_matches(payload, current)` in `core/security.py`:
  - `tv` equal to the account's counter → valid;
  - `tv` absent (a token minted before this shipped) → read as **0**, so the
    upgrade does not log users out — and it dies at the first revocation like
    everything else, so it is not a bypass;
  - `tv` non-numeric → mismatch (fail closed), never an exception.
- Enforced in **both** paths:
  - `api/deps.get_current_user` — every authenticated request;
  - `AuthService.refresh_tokens` — the path that actually mattered.

  Both checks sit **after** the account-status check, so a suspended account
  still reports suspension (`403 PERMISSION_DENIED` / `401
  AUTHENTICATION_FAILED`) rather than a generic revocation. No existing response
  contract changed.

### 2.3 Revocation

`AuthService.revoke_all_sessions(db, user_id, reason, actor_id=None, commit=True)`
increments the counter under `SELECT … FOR UPDATE` (a real row lock on
PostgreSQL; SQLite ignores the clause) and writes a `SESSIONS_REVOKED` audit row
carrying the reason and the before/after version. `commit=False` lets a caller
fold the eviction into its own transaction.

Call sites wired today:

| Call site | Reason recorded | Notes |
| --- | --- | --- |
| `POST /auth/logout` | `USER_LOGOUT` | New endpoint; revokes account-wide |
| Admin status change to `SUSPENDED` / `CLOSED` | `ADMIN_STATUS_*` | `commit=False` — atomic with the status change |

`revoke_all_sessions` is what WS-4/LB-1 will call on password reset; that call
site arrives with the reset flow.

### 2.4 Endpoint

`POST /api/v1/auth/logout` (authenticated). Increments the version and returns
`{user_id, sessions_revoked: true, token_version}`. The token used to call it is
invalidated too, so a **second** logout with the same token returns 401 — which
clients should treat as "already signed out", not as an error.

### 2.5 Why logout is account-wide

Revoking everything is coarse: signing out on a phone also signs the user out on
their other devices. The alternative — the roadmap's optional per-`jti` denylist
— would add a Redis round-trip to **every authenticated request** on the hot
path and a new availability dependency on Redis for ordinary API calls, in
exchange for a UX nicety. The roadmap already allows coarse for launch. It was
therefore **not built**, and this document does not claim it exists. The
trade-off is visible to users (logout is account-wide) and to clients (the
`SESSION_REVOKED` code).

### 2.6 A distinct error code

`SessionRevokedError` → `401 SESSION_REVOKED`, separate from
`AUTHENTICATION_FAILED`. Clients can distinguish "your session was deliberately
ended" (route to login, no retry) from "this token expired" (attempt a refresh),
and support can tell which happened without reading server logs. `code` is a
free-form string in `ProblemDetail`, so this is additive to the published
contract.

---

## 3. Verification

### 3.1 The four acceptance criteria from the roadmap

| Criterion | Test |
| --- | --- |
| Refresh token issued before a revocation is rejected after it | `test_refresh_token_issued_before_revocation_is_rejected` |
| Access token issued before a revocation is rejected after it | `test_access_token_issued_before_revocation_is_rejected` |
| Logout invalidates the refresh token | `test_logout_invalidates_the_refresh_token` |
| Admin suspension immediately invalidates active sessions | `test_admin_suspension_immediately_invalidates_active_sessions` |

### 3.2 The cases a careless implementation would pass anyway

These are the tests that make the claim mean something:

- **Re-activation must not resurrect tokens.** A status check *already* refuses
  a suspended user's requests — so a suspension test passes with no revocation
  at all. The test suspends, asserts refusal, re-activates, and asserts the
  pre-suspension access **and** refresh tokens are still dead. This is the
  behaviour that was missing before: block a user, unblock them, and every token
  they still held came back to life.
- **Blast radius.** One user's logout must not sign another user out — guards
  against a counter stored anywhere but per-user (a global epoch would pass
  every other test and evict the whole customer base on one logout).
- **Increment, not set.** Two revocations must produce versions N+1 and N+2; an
  implementation that *sets* the version would let a token minted between them
  survive the second.
- **Logging back in must work.** "Evict everything, then hand back tokens that
  are already stale" bricks every account that logs out. Asserted directly.
- **Legacy tokens.** A token minted before this feature (no `tv`) is accepted on
  a version-0 account and refused after the first revocation.
- **Audit.** `SESSIONS_REVOKED` is recorded against the user.
- **Migration shape.** `test_migration_003_adds_a_backfilled_not_null_token_version`
  runs the real Alembic chain against a throwaway SQLite database, asserts the
  column is `NOT NULL` with a server default, and proves a row inserted without
  naming it backfills to 0. The rest of the suite builds its schema from the
  models, which is the code path that let the 001 drift ship.

### 3.3 Negative controls (tests proven to detect the defect)

The suite was run against three deliberately broken builds, from a committed
tree so the source could be restored safely:

| Control | Result |
| --- | --- |
| Access-token check removed from `deps.get_current_user` | 6 tests fail |
| Refresh-token check removed from `refresh_tokens` | 3 tests fail — including a `200 OK` on refreshing with a revoked token, i.e. the original defect reproduced |
| Admin suspension no longer evicts | exactly 1 test fails (the re-activation case) |

The third is the useful one: it shows the other suspension assertions pass
without eviction, and that the re-activation assertion is what pins the fix
down.

### 3.4 Full suite

```
$ .venv/bin/python -m pytest -q -p no:logging
133 passed, 2 skipped, 1 warning in 78.44s
```

Two skips, both pre-existing and neither related: the real-Redis integration
test (runs in CI with `PAYDAY_TEST_REDIS_URL`) and the `alg=none` token test the
local JOSE library refuses to mint.

---

## 4. Files touched

| File | Change |
| --- | --- |
| `alembic/versions/003_add_token_version.py` | New migration (add/drop column) |
| `src/payday/models/user.py` | `token_version` column |
| `src/payday/core/security.py` | `tv` claim, required argument, `token_version_matches` |
| `src/payday/core/exceptions.py` | `SessionRevokedError` |
| `src/payday/api/deps.py` | Version check in `get_current_user` |
| `src/payday/services/auth_service.py` | Version checks; `revoke_all_sessions` |
| `src/payday/api/v1/auth.py` | `POST /auth/logout` |
| `src/payday/api/v1/admin.py` | Eviction folded into the status-change transaction |
| `tests/test_sprint6_session_revocation.py` | New, 12 tests |
| `tests/conftest.py`, `tests/test_sprint3_rbac_security_boundaries.py`, `tests/test_sprint4_pilot_business_day.py`, `tests/test_sprint4_security_audit.py` | Token mints updated for the now-required argument |
| `docs/api/openapi-baseline.json` | Regenerated: `POST /api/v1/auth/logout` added, 33 paths, nothing removed or changed |

---

## 5. Deliberately not done

| Item | Why |
| --- | --- |
| Per-`jti` denylist (single-device logout) | Roadmap marks it optional for launch; it puts Redis on the hot path of every authenticated request. Logout is account-wide and documented as such. |
| `set-pin` as a revocation call site | The roadmap lists "PIN reset" as a call site. No PIN-*reset* flow exists: `POST /auth/set-pin` is how a new account sets its first PIN (requires the account password), so revoking there would sign a user out mid-onboarding for no security gain — an attacker holding only a stolen session cannot change the PIN without the password. The call site belongs with the LB-1 flow, which is where D15 (does reset clear the PIN?) gets decided. |
| Session listing / "log out other devices" UI | Needs per-session tracking that does not exist; not a launch blocker. |
| Refresh-token rotation with reuse detection | Related but separate hardening; not in the roadmap's WS-2 deliverables. Rotation *without* reuse detection would add the false impression of safety without the detection. |

---

## 6. Effect on the remaining work

- **WS-4 (LB-1) is unblocked from the LB-7 side.** The password-reset flow must
  call `AuthService.revoke_all_sessions(db, user_id, reason="PASSWORD_RESET")`
  on the leg that sets the new password hash. A reset that does not call it is
  an unshipped fix, not a partial one.
- **Suspension is now durable.** Previously, re-activating a blocked account
  silently restored its sessions.
- **R8 still applies.** Nothing in this workstream depends on CI, but the
  workflows still cannot be pushed, so the migration has not run on PostgreSQL
  in CI. It has been exercised on SQLite via the Alembic chain and is written
  with plain `ALTER TABLE ADD/DROP COLUMN`, which both dialects support.

## 7. How to re-run

```bash
.venv/bin/python -m pytest tests/test_sprint6_session_revocation.py -q -p no:logging
.venv/bin/python -m pytest -q -p no:logging          # full suite
```
