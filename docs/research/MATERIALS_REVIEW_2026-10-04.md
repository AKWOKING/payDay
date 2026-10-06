# Materials review — five videos, and what we take from each

**Date:** 2026-10-04 · **Author:** Lead System Architect
**Instruction:** "learn and integrate, keeping in mind our context — things that will make the
project better/efficient, anything else discard."

**Verdict summary**

| # | Video | Verdict | What changes in the project |
| --- | --- | --- | --- |
| 1 | *How to Build an APP Backend for 100% FREE* (InsForge/BaaS) | **Discarded** | Nothing. Disqualifying for a licensed wallet — see §1 |
| 2 | *Beginner's Guide to Mobile App System Design* (Philipp Lackner, 37 min) | **Adopted** | New client-behaviour rules in the integration guide (§13) — offline, retries, polling, caching on Cameroonian networks |
| 3 | *Top Backend Choices for Flutter Devs* (7 min) | **Discarded** (one validation) | Confirms the relational-ledger + language-agnostic-contract choices we already made |
| 4 | *Full Architecture of a Real AI Mobile App* (29 min) | **Partly adopted** | Layer-responsibility rules now enforced by tests; validates the adapter/service/config layering we already use |
| 5 | *Frontend vs Backend vs Database: 5 Layers* (20 min) | **Partly adopted** | Layer table + the observability gap it exposed: `/admin/ops/overview`, registered as **LB-19** |

**Retrieval honesty.** Full transcripts were readable for videos 1, 3 and the openings of 2,
4 and 5; YouTube stopped serving transcript past roughly the first ten minutes for the longer
ones. Where a claim below rests on the video's stated structure rather than its spoken
argument, it says so. Nothing here is attributed to a section that was not read.

---

## 1. Video 1 — "free backend" (InsForge): discarded

An 11-minute pitch for a Backend-as-a-Service: hosted Postgres, auth, storage, MCP server,
free to 50k monthly active users, US region, connect-from-your-IDE. The engineering advice is
thin and the offer is irrelevant to us in a way worth recording, because "free backend" is a
temptation a lean startup will meet again:

* **Data residency is a licence question for us, not a preference.** Cameroon's Law 2024/017
  requires prior authorisation for cross-border processing of personal data, and D6 (storage
  jurisdiction) is an open launch decision. A US-region BaaS decides that question by accident.
* **The ledger would sit outside our control.** A wallet's balances, transaction history and
  audit trail are the evidence a COBAC-supervised licensee or partner bank audits. Free-tier
  limits (500 MB database, 5 GB bandwidth) and an unversioned hosted schema are not
  substitutable for Alembic migrations and a database we can restore ourselves.
* **MCP-driven schema changes are the opposite of our migration discipline.** Our whole
  correctness story (migration/model parity tests, contract drift baseline, negative controls)
  assumes schema changes are reviewed, versioned and reversible.
* **Vendor lock-in on the money path** is the largest single risk to the licence/partnership
  routes in `docs/research/API_ACCESS_MTN_OM_CAMEROON.md`.

One idea worth keeping on a shelf, not in the code: an MCP server over our own OpenAPI spec
would let the frontend engineer's tooling scaffold typed clients faster. Deferred — it needs a
dependency and a security review, and the OpenAPI 3.1 document plus the integration guide
already serve that purpose.

## 2. Video 2 — mobile app system design: adopted (the useful one)

The framing is three zoom levels: high level (who uses it, what it does, non-functional
requirements, scale), architecture (patterns and practices), tech stack (protocols, real-time,
API design, DB schema, caching, offline). The part that matters for us is the **non-functional**
one, because it names our actual constraints:

* an older, low-end Android user base → memory and battery budgets, not just latency;
* intermittent networks (train, tunnel, rural 2G/3G) → offline-first thinking, retries, and a
  UI that distinguishes *unknown* from *failed*;
* metered data → payload discipline and deliberate caching, not "cache everything";
* real-time connections are battery-expensive → prefer polling over a socket unless the
  product needs live updates.

Money changes the usual answers, so the client rules we adopted are not the video's generic
"offline-first" recipe — queuing writes offline is exactly wrong for payments. They are in
**`docs/FRONTEND_INTEGRATION_GUIDE.md` §13**: no optimistic success, retry only with the same
`idempotency_key`, timeout ≠ failure, poll with backoff until a terminal state, cache reads
with an explicit "as of" time and never cache to decide whether money moved.

## 3. Video 3 — backend choices for Flutter: discarded

A survey of No-backend / BaaS (Firebase, Appwrite, Pocketbase, Supabase) / Dart backends
(Dart Frog, Serverpod) / other (Node, Go, Laravel, Flask, **FastAPI**, Django). Our stack was
decided long before this review and nothing here changes it. Two useful validations:

* its critique of NoSQL for relational data (users → posts → comments) is the mild version of
  our problem: a double-entry-ish ledger is *inherently* relational, and Postgres was the right
  call rather than Firestore;
* "Flutter doesn't care what generates the JSON" — the contract is the OpenAPI document, not
  the language. That is precisely why the contract baseline and the integration guide, not our
  Python, are the frontend engineer's interface.

## 4. Video 4 — architecture of a real mobile app: partly adopted

A layered walkthrough: infrastructure (Dockerfile, compose) → configuration (env into typed
structs) → persistence (models + generated data access) → services (interfaces for external
providers, so the provider is swappable) → controllers. What we already do, and now enforce:

* **Provider-agnostic interfaces** — our `adapters/base.py` + `factory.py` is the same pattern
  for MTN/Orange/simulator; the video reaches for it for LLMs.
* **Services own external calls; controllers do not** — mostly true here, with one honest
  exception (`api/v1/webhooks.py` parses operator callbacks), now a documented, asserted
  exception rather than an unstated drift.
* **Config from the environment into typed settings** — `core/config.py`; the video's nested
  structs are the same idea.

**Rejected from this video:** declarative models with *generated* migrations ("no manual
migrations"). Convenient for a calorie tracker; wrong for a ledger. Our Alembic revisions are
reviewable, testable and reversible, and migration/model parity is asserted — that is a
deliberate cost, not an accident.

## 5. Video 5 — the five layers: partly adopted

Frontend → backend → database → external services → analytics/monitoring, with the argument
that each layer has one job, layers doing each other's jobs is how systems break, and that the
UI is the *easiest* thing to change while the data model, auth and contracts are the hard parts.
Two things came out of it:

1. **The layer rules are now executable.** `tests/test_architecture_layers.py` parses every
   module with `ast` and fails if a layer imports upward (adapters ↛ services/api, services ↛
   api, models/core ↛ services/adapters/api, schemas ↛ services/adapters/api, api ↛ adapters
   except the documented webhook boundary). It also fails if the documented exception stops
   being needed — exceptions rot into permissions.
2. **The missing layer was real.** We had no analytics/monitoring beyond health probes and
   logs: nothing answered "is money stuck, is one operator broken, is the sweep still
   switched on". `GET /api/v1/admin/ops/overview` now answers those three questions from the
   ledger, and **LB-19** registers what is still missing (metrics scraping, alert routing).

The video's "UI is the easiest to change, data model is the hardest" also validates where we
have spent effort — ledger conservation, token revocation, contract baselines — versus where
the deployed frontend site is wrong (LB-14: claims, not code).

---

## 6. Sources

1. *How to Build an APP Backend for 100% FREE | Flutter, React Native & Expo* — The Metaverse
   Guy, 2026-03-27 (11:56) — https://youtu.be/4ezhzJDJ6Jw
2. *Beginner's Guide to Mobile App System Design (+ Tips for Interviews!)* — Philipp Lackner,
   2026-05-03 (37:01) — https://youtu.be/OP6JHa21We8
3. *Top Backend Choices for Flutter Devs with Tyler* — Tyler Codes, 2024-02-12 (07:11) —
   https://youtu.be/4ula6xAW_0A
4. *Full Architecture of a Real AI Mobile App (Backend, Frontend, Infra)* — Eugen Bondarev,
   2025-12-26 (29:11) — https://youtu.be/7Mmez0fvwLM
5. *Frontend vs Backend vs Database: How to Build Real Apps in 2025* — Sunny Israni,
   2025-12-04 (20:47) — https://youtu.be/Ufr65eViTeE
