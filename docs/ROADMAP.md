# payDay Delivery Roadmap

**Status:** Draft — discovery gate is open  
**Baseline reviewed:** 2026-08-28  
**Engineering role:** system architecture, computer engineering, implementation, security, quality, and operations  
**Frontend ownership:** visual/UI design is owned by the frontend developer; this roadmap defines the technical handoff and integration responsibilities.

## 1. Executive readout

This repository is a clean starting point, not an existing application. The baseline review found:

- one tracked file: `README.md`, containing only `# payDay`;
- no application source, package manifest, database schema, tests, CI/CD configuration, infrastructure, design files, or technical documentation;
- no GitHub issues, pull requests, detected language, or declared license;
- no product brief or requirements that define what “payDay” means.

Therefore, there is no implementation to reverse-engineer yet. The first engineering milestone is **product and risk discovery**, followed by an intentionally small, observable vertical slice. The roadmap below is the sequence I will use unless a documented decision changes it.

The name suggests a payroll, earned-wage, payments, wallet, or personal-finance product, but that is not enough to select a domain model or compliance boundary. Until the product is clarified, we will treat financial and personally identifiable data as sensitive and avoid irreversible design commitments.

> **Important:** this roadmap is a plan, not a claim that the product requirements are already understood. Product mode, jurisdiction, users, money movement, and integrations are P0 decisions.

## 2. Research-informed engineering position

Research was performed against current primary or standards sources on 2026-08-28. The resulting principles are:

| Finding | How it affects payDay |
| --- | --- |
| The [NIST Secure Software Development Framework (SSDF)](https://csrc.nist.gov/projects/ssdf) treats security as a lifecycle practice and includes requirements, risk, provenance, secure environments, and release protection. | Security requirements, threat modeling, dependency provenance, and release evidence begin during discovery and remain part of every feature. |
| [OWASP ASVS 5.0.0](https://owasp.org/www-project-application-security-verification-standard/) provides testable controls for modern web applications and services. | We will select and tailor an ASVS verification level after classifying the product. A sensitive financial system will start from at least a Level 2 control set unless a security review justifies another level. |
| [OpenAPI 3.2.0](https://spec.openapis.org/oas/v3.2.0.html) is the current published OpenAPI specification found during research. | The backend/frontend boundary will be contract-first. If tooling is not ready for 3.2.0, use the latest supported 3.1.x dialect deliberately and record the reason in an ADR. |
| [RFC 9457](https://datatracker.ietf.org/doc/html/rfc9457) defines machine-readable Problem Details for HTTP APIs. | Validation, authorization, conflict, rate-limit, and dependency errors will have a stable machine-readable shape; UI copy will not depend on parsing free-form error text. |
| [NIST SP 800-63-4](https://csrc.nist.gov/pubs/sp/800/63/4/final) updates guidance for identity proofing, authentication, and federation. | Authentication assurance, MFA, recovery, and privileged-action controls will be risk-based rather than improvised in application code. |
| OWASP guidance on [authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html), [business logic](https://cheatsheetseries.owasp.org/cheatsheets/Business_Logic_Security_Cheat_Sheet.html), [transaction authorization](https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html), and [payment gateway integration](https://cheatsheetseries.owasp.org/cheatsheets/Third_Party_Payment_Gateway_Integration_Cheat_Sheet.html) emphasizes server-side invariants, explicit state transitions, idempotency, replay resistance, and authorization on every request. | Amounts, ownership, permissions, workflow state, payment status, and totals will be derived and enforced server-side. External callbacks will be authenticated, reconciled, and safe to retry. |
| [WCAG 2.2](https://www.w3.org/TR/WCAG22/) is a W3C Recommendation. | The frontend developer’s design handoff and the implemented experience will include keyboard, focus, semantic, contrast, error, responsive, and assistive-technology acceptance criteria. |
| [PCI DSS v4.0.1](https://www.pcisecuritystandards.org/standards/pci-dss/) is relevant only if the product stores, processes, or transmits payment-card data or falls into a related service-provider scope. | We will establish the card-data boundary before choosing a payment integration. The preferred default is hosted/tokenized provider flows, not storing raw card data. Compliance claims require a qualified review. |

These references guide engineering; they do not replace legal, tax, payments, security, or compliance advice for the jurisdictions in which the product will operate.

## 3. Discovery gate: decisions required before feature implementation

The following must be answered and recorded in a product brief. Unknowns are not to be silently converted into architecture assumptions.

### P0 product questions

1. **What is payDay?** Choose and describe the first release: payroll administration, earned-wage/pay advance, payment/wallet/settlement, personal-finance tooling, or another product.
2. **Who are the actors?** Identify end users, employers/administrators, approvers, support staff, finance/reconciliation staff, and service accounts. Define whether multiple organizations/tenants are supported.
3. **What is the single most valuable end-to-end job for MVP?** State the trigger, happy path, failure paths, and measurable outcome.
4. **Does the system move money, calculate money, or only display money?** Identify currencies, precision/rounding rules, funding/settlement source, payout rails, refunds, reversals, chargebacks, and reconciliation responsibilities.
5. **Where will it operate?** Confirm countries/states, currency, tax/payroll rules, data residency, language, timezone, retention, deletion, and regulatory obligations. Do not assume a US or single-country model.
6. **What external systems are required?** Examples include identity provider, bank/payment provider, payroll/tax provider, email/SMS/push, accounting, file storage, and webhooks. Record ownership and sandbox availability.
7. **What trust and access model is needed?** Confirm self-serve vs invitation, identity proofing, MFA, delegated access, approval limits, separation of duties, support impersonation policy, and audit requirements.
8. **What are the operational targets?** Expected users and transaction volume, peak periods, uptime/SLO, latency, RTO/RPO, support hours, budget, and deployment/cloud constraints.
9. **What does success mean?** Product KPI, correctness KPI, security KPI, operational KPI, and pilot exit criteria.

### Frontend design handoff contract

The visual design remains the frontend developer’s responsibility. To build a correct backend and integration, the handoff must include:

- screen/route inventory mapped to user journeys and roles;
- design source links, tokens, typography, component states, responsive breakpoints, and supported browsers/devices;
- loading, empty, validation-error, server-error, unauthorized, forbidden, conflict, retry, and offline/degraded states;
- field-level rules, formatting, localization, currency/date/timezone behavior, and copy ownership;
- keyboard/focus behavior, semantic requirements, contrast expectations, and target WCAG 2.2 level;
- required API data, mutations, optimistic/concurrency behavior, pagination/filtering, and refresh expectations;
- analytics events and privacy constraints, if analytics are in scope;
- acceptance screenshots or prototypes for the highest-risk workflows.

The engineering team will not redesign the interface. We will challenge designs only where security, accessibility, browser behavior, data protection, or a stated product invariant would be violated, and we will propose the smallest compliant adjustment.

### Discovery outputs and gate

The discovery gate closes when these artifacts are approved:

- product brief, glossary, personas, journey map, and MVP/non-MVP list;
- user stories with acceptance criteria and failure/permission cases;
- non-functional requirements and measurable success metrics;
- data classification and regulatory/compliance applicability decision;
- initial threat model and abuse cases;
- frontend handoff contract;
- initial integration/provider inventory;
- prioritized backlog and release/pilot definition.

## 4. Target architecture (starting hypothesis)

No technology stack is committed by the current repository. After discovery, I will create an ADR using team skill, hosting constraints, support horizon, security tooling, performance, cost, and ecosystem fit as decision criteria. The working default is a **modular monolith with a relational source of truth**, not microservices.

```text
Frontend application (frontend developer)
                |
       HTTPS / same-origin API or BFF
                |
  API boundary: authn, validation, rate limits, contracts
                |
  Application modules / domain services / workflow state machines
       |                 |                    |
 relational database  job worker (only if needed)  provider adapters
       |                 |                    |
 migrations + audit   notifications/events     identity/payments/etc.
                |
 logs + metrics + traces + alerts + operational audit
```

### Logical boundaries

These are boundaries, not a commitment to separate deployables:

- **Identity and tenancy:** users, organizations, memberships, roles, session context, and tenant isolation.
- **Core payDay domain:** the business concepts discovered in the product brief; business rules stay independent of HTTP and provider SDKs.
- **Money/ledger:** conditional but mandatory if balances, earnings, advances, payouts, fees, or settlements exist. Use integer minor units or a correctly configured decimal type; never binary floating point for monetary values. For actual value movement, prefer an immutable, auditable double-entry ledger with derived balances.
- **Workflow and approvals:** explicit server-side states and allowed transitions, including expiry, rejection, reversal, and retry semantics.
- **Integrations:** ports/adapters around third-party providers; provider identifiers, raw payloads, signatures, and normalized status are separated. Provider callbacks are not trusted because a browser was redirected.
- **Notifications:** email/SMS/push delivery, templates, preferences, retries, and suppression of sensitive content.
- **Audit and support:** append-only security/business audit events, actor, tenant, action, target, outcome, timestamp, correlation ID, and reason where appropriate. Logs must not contain secrets or unnecessary PII.

### Data and persistence defaults

- A transactional relational database is the source of truth for authoritative state.
- All schema changes are migration-reviewed, forward-compatible during deploy, and tested against rollback/roll-forward procedures.
- IDs are opaque and non-sequential at public boundaries; timestamps are stored consistently and rendered in the user’s relevant timezone.
- PII is minimized, classified, encrypted in transit and at rest, access-controlled, retained only as long as required, and excluded from test fixtures unless synthetic.
- Caches and queues are never the authoritative ledger. Redis or an equivalent is optional for rate limiting, short-lived state, or cache acceleration.
- Object storage is optional for documents; uploads require type/size validation, malware scanning where appropriate, private storage, signed access, and retention rules.
- Backups are encrypted, access-controlled, monitored, and regularly restored in a non-production exercise.

### Stack decision gate

Before creating application scaffolding, record an ADR covering:

- language/runtime and framework;
- API style and validation/schema tooling;
- relational database and migration approach;
- identity provider/session model;
- queue/worker and notification strategy;
- cloud/runtime, IaC, secret manager, and observability stack;
- local development and test dependencies;
- licensing, support lifecycle, supply-chain, and total-cost constraints.

The simplest stack that satisfies the product and risk requirements wins. A microservice split requires a demonstrated bounded context, independent scaling/deployment need, or isolation requirement; it is not the starting point.

## 5. Interface and contract standards

The API contract is a collaboration surface between backend and the frontend developer.

- Maintain a versioned OpenAPI document in the repository and review it like code.
- Prefer resource-oriented HTTP APIs with JSON unless discovery proves another protocol is required.
- Use `application/problem+json` based on RFC 9457 for machine-readable errors; provide stable error types/codes and field pointers.
- Define authentication and authorization requirements per operation, not only at the route group.
- Validate input shape and business meaning server-side. Never accept client-supplied totals, permissions, ownership, or payment status as authoritative.
- Use opaque resource IDs, consistent pagination/filtering/sorting, explicit nullability, and UTC/ISO 8601 timestamps.
- Require an idempotency key for retryable non-idempotent operations; persist the result or an equivalent operation record and define conflict/expiry behavior.
- Use optimistic concurrency or transactional locking for sensitive state transitions; make replay, duplicate delivery, timeout, and provider-outage behavior explicit.
- Return correlation/request IDs. Never expose stack traces, tokens, credentials, raw provider secrets, or unnecessary personal data.
- Generate frontend mocks/clients from the contract where helpful, but do not let generated code hide the domain rules.

## 6. SDLC roadmap and exit gates

This is a gated sequence. Work can run in parallel within a stage, but the exit criteria must be met before the next dependent stage is called complete.

### R0 — Product discovery and scope lock

**Outcome:** an agreed MVP problem and a risk-aware product brief.

Work:

- answer the P0 questions and select the product mode;
- map actors, permissions, journeys, states, invariants, and abuse cases;
- classify data and determine jurisdiction/provider/compliance boundaries;
- receive the frontend design handoff and map routes to API capabilities;
- define MVP, non-MVP, success metrics, operational targets, and pilot cohort.

**Exit gate:** product owner and engineering sign off on the product brief, backlog, non-functional requirements, and open-risk register. No critical unknown is hidden in an implementation task.

### R1 — Architecture, threat model, and technology decisions

**Outcome:** a buildable design with explicit trade-offs.

Work:

- write the context/container/component architecture and data-flow diagrams;
- identify trust boundaries, threats, abuse cases, mitigations, and residual risk;
- choose stack, auth, database, provider boundary, deployment model, and observability;
- define domain invariants, state machines, transaction/ledger approach if applicable;
- write ADRs for decisions that are costly to reverse.

**Exit gate:** architecture review completed; P0 security, compliance, money-correctness, and availability risks have an owner and mitigation; implementation sequence is agreed.

### R2 — Repository and delivery foundation

**Outcome:** a new contributor can run, test, lint, and deploy a safe skeleton.

Work:

- add source layout, package/dependency management, formatting, linting, type/static checks, and contribution guidance;
- add CI for reproducible install, unit tests, integration tests, contract validation, secret scanning, dependency/SBOM checks, and build artifacts;
- establish local, CI, staging, and production configuration boundaries; no secrets committed;
- add health/readiness endpoints, structured logging, correlation IDs, error handling, and baseline metrics;
- provision database migrations, least-privilege service accounts, encrypted secrets, and IaC where appropriate;
- add an initial threat model, data handling rules, and secure coding checklist.

**Exit gate:** clean clone passes CI; local and staging skeletons are deployable; failure is observable; a secret or production PII cannot be required to run tests.

### R3 — Domain model and API contract

**Outcome:** frontend and backend can work against one stable behavioral contract.

Work:

- create the domain glossary and canonical model;
- define schemas, constraints, indexes, migrations, audit events, retention, and seed data;
- publish OpenAPI schemas, auth requirements, problem types, examples, and mocks;
- implement authorization matrix and tenant/resource checks;
- define idempotency, concurrency, retries, webhook verification, and external-provider failure behavior;
- review contract with frontend developer against every design state.

**Exit gate:** contract tests pass; example requests/responses cover happy, validation, authorization, conflict, retry, and provider failure cases; migrations and invariants are reviewed.

### R4 — First vertical slice

**Outcome:** one highest-value journey works end-to-end in a test/staging environment.

Work:

- implement the smallest complete path from authenticated user to persisted domain result and frontend state;
- include at least one negative/permission path and a retry/reload path;
- verify audit, metrics, logs, notification/provider boundary, and support visibility;
- exercise real migration, contract, integration, and browser tests;
- validate the design handoff without expanding scope into unrelated polish.

**Exit gate:** product owner demonstrates the slice; tests cover business invariants and authorization; no known critical security/data-integrity defect; operational signals are usable.

### R5 — MVP feature increments

**Outcome:** the rest of the prioritized MVP is delivered as thin vertical slices.

For every slice, follow this order: acceptance criteria → contract/model → authorization and threat review → implementation → tests → telemetry/docs → frontend integration → demo. Prioritize in this order unless product evidence changes it:

1. identity, onboarding, tenant/membership setup, and recovery;
2. core value workflow and its normal failure states;
3. review/approval/separation-of-duties flow if required;
4. money/ledger/payout/reconciliation capability if in scope;
5. notifications and user preferences;
6. history, receipts/statements, export, and support/audit views;
7. edge cases, accessibility, localization, and responsive behavior.

**Exit gate:** all MVP acceptance criteria and explicit non-MVP exclusions are reviewed; the release candidate satisfies the Definition of Done below.

### R6 — Hardening and verification

**Outcome:** the release candidate is secure, correct, usable, recoverable, and supportable.

Work:

- run ASVS-based security verification, SAST/DAST/dependency/secret/IaC scans, and targeted penetration testing;
- test authorization matrices, cross-tenant access, session/recovery, rate limits, abuse controls, replay, duplicate callbacks, race conditions, and state transitions;
- test monetary calculations with boundary, rounding, currency, reversal, and reconciliation cases if relevant;
- run accessibility review against WCAG 2.2 target, keyboard/screen-reader checks, and responsive/browser matrix;
- run performance/load/soak tests against agreed SLOs and peak-period scenarios;
- validate backups, restore, migration, rollback/roll-forward, disaster recovery, and incident procedures;
- remove debug routes/data, review headers/CSP/CORS/cookies, and confirm privacy/retention behavior.

**Exit gate:** all P0/P1 findings are closed or formally accepted by the accountable owner; evidence is stored with the release; data-integrity and recovery exercises pass.

### R7 — Staging, pilot, and production release

**Outcome:** a controlled release with a rollback and support plan.

Work:

- create a production-like staging environment with synthetic or approved test data;
- run smoke, contract, critical-path E2E, migration, and provider sandbox tests;
- perform a limited pilot with feature flags/allow-listing where possible;
- prepare release notes, user/support documentation, status communication, runbooks, escalation contacts, and rollback steps;
- verify dashboards, alert routing, on-call ownership, backup freshness, rate limits, and provider credentials;
- execute go/no-go review with product, engineering, security/compliance, and operations.

**Exit gate:** pilot success metrics and correctness checks pass; no release-blocking findings; accountable owners approve production.

### R8 — Operate, learn, and expand

**Outcome:** the product remains reliable and improves from evidence.

Work:

- monitor SLOs, errors, latency, job/provider failures, authorization denials, reconciliation, support volume, and product KPIs;
- conduct incident/post-incident reviews and feed actions into the backlog;
- patch dependencies, rotate secrets, review access, rebuild SBOM/release evidence, and re-run security checks;
- review product analytics with privacy safeguards; remove unused data and features;
- revisit architecture only when measured load, team boundaries, risk, or product scope warrants it.

**Exit gate:** an operating review confirms current risks, costs, capacity, support readiness, and the next validated increment.

## 7. Workstream ownership and collaboration

| Workstream | Engineering responsibility | Frontend developer / other partner input |
| --- | --- | --- |
| Product/domain | domain model, invariants, state machines, API behavior, risk questions | product intent, user journeys, acceptance criteria |
| Visual/UI | integration behavior, data states, accessibility/security constraints, performance of API usage | visual design, components, responsive layouts, copy/presentation |
| Backend | application modules, persistence, integrations, jobs, audit, error handling | required fields, interactions, loading/error/empty states |
| Data | schema, migrations, retention, backup/restore, reporting correctness | display/formatting needs and export expectations |
| Security/privacy | threat model, authn/authz, secrets, data minimization, verification evidence | safe UX for consent, recovery, transaction confirmation, sensitive states |
| Platform/operations | CI/CD, environments, IaC, observability, release and incident runbooks | preview/staging integration and browser/runtime constraints |
| Quality | unit/integration/contract/E2E/performance/security/accessibility strategy | testable selectors/states and cross-browser acceptance |

Frontend design is a parallel input, not a prerequisite for backend domain discovery. However, no endpoint is considered complete until its user-visible states and handoff behavior are agreed.

## 8. Security, privacy, and financial correctness baseline

These controls are defaults until the product risk assessment strengthens or narrows them:

- deny by default; authenticate and authorize every protected operation at the server/domain boundary;
- isolate tenant data and test for guessed IDs, bulk access, privilege changes, support access, and export leakage;
- use a managed identity provider where practical; secure cookies/session handling or a documented token model; MFA for administrators and risk-sensitive actions; safe recovery and re-authentication;
- keep secrets in a secret manager, rotate/revoke them, limit access, and prevent plaintext secrets or sensitive PII from logs, traces, analytics, crash reports, and client storage;
- use TLS in transit and encryption at rest; document key ownership, rotation, and recovery;
- derive amounts, prices, permissions, identities, ownership, and provider status server-side;
- if value moves, use an immutable ledger/operation record, integer minor units or exact decimal arithmetic, explicit rounding, idempotency, atomic state changes, reconciliation, and auditable reversals;
- verify provider webhook signatures, expected amount/currency/operation, timestamp/replay protection, and current server-side status before fulfillment or credit;
- apply rate limits, abuse controls, caps, anomaly signals, and transaction confirmation appropriate to the risk;
- minimize and classify PII; define purpose, retention, deletion/export, legal hold, and data-subject/support procedures before production;
- log security-relevant events with actor, target, result, and correlation context while avoiding secrets and unnecessary sensitive values;
- maintain dependency inventory/SBOM, pinned/reproducible builds where practical, vulnerability response, and disclosure/incident procedures.

No compliance certification will be claimed from this roadmap alone. If payroll, lending, money transmission, card data, tax filing, KYC/AML, or employment data is in scope, obtain the appropriate qualified legal/compliance/provider review before launch.

## 9. Quality strategy and CI/CD gates

### Test layers

- **Domain unit tests:** calculations, invariants, state transitions, permissions, rounding, expiry, and reversal rules.
- **Integration tests:** real database migrations/queries, transaction boundaries, queues, storage, and provider adapters using sandboxes or deterministic fakes.
- **Contract tests:** OpenAPI schema, status codes, examples, problem types, backward compatibility, and frontend mocks.
- **Authorization regression tests:** role × resource × action × tenant matrix, including negative cases.
- **End-to-end browser tests:** the critical user journeys, reload/retry, empty/error/forbidden states, and the frontend handoff.
- **Adversarial tests:** replay, duplicate requests/callbacks, concurrent updates, guessed IDs, rate limits, malformed input, dependency failure, and partial outage.
- **Security/accessibility/performance tests:** automated checks plus targeted manual review; coverage thresholds are chosen by risk and maintained as a signal, not used as a vanity target.

### Pull request and release gates

Every PR should have reviewable scope, tests, docs/contract changes, migration safety, and no new high-severity findings. CI should run formatting/lint/type checks, unit/integration/contract tests, secret scanning, dependency vulnerability checks, and build verification. Release CI adds E2E smoke, artifact/SBOM evidence, migration checks, security verification, and environment promotion approvals.

## 10. Risks and decision register

| Risk | Impact | Control / trigger |
| --- | --- | --- |
| Product meaning is ambiguous | Rework, wrong data model, wrong compliance posture | Close R0 before feature build; record product-mode ADR. |
| Financial scope is underestimated | Incorrect balances, loss, regulatory exposure | Treat money as a separate risk review; ledger, idempotency, reconciliation, provider sandbox, qualified review. |
| Jurisdiction is chosen too late | Illegal or unusable behavior | Capture geography, currency, tax/privacy obligations in the product brief. |
| Frontend design lacks behavioral states | Integration churn and inaccessible/error-prone UX | Require the handoff contract; review API examples against all states. |
| Overengineering too early | Slow delivery and operational cost | Modular monolith default; justify every separate service. |
| External provider outage/replay | Duplicate payment/credit or stuck workflow | Adapter boundary, server-side verification, explicit state machine, retries, reconciliation, alerts. |
| Sensitive data leaks through tooling | Privacy/security incident | Data classification, synthetic fixtures, log/analytics redaction, least privilege, secret scanning. |
| Empty repo leads to weak delivery discipline | “Works locally” and unreviewable changes | R2 foundation before feature work; CI, docs, environments, and release evidence. |

The first ADR set will be: product mode; stack/runtime; identity and authorization; data/ledger; API/versioning; provider boundary; deployment/observability; compliance/data retention. ADRs record context, decision, alternatives, consequences, and reversal triggers.

## 11. Definition of Ready and Definition of Done

### A feature is ready when

- the user, problem, value, scope, acceptance criteria, and non-goals are clear;
- roles, tenant boundaries, state transitions, invariants, and abuse cases are identified;
- the frontend route/states and API contract are mapped;
- data classification, retention, migration, and integration impacts are known;
- test cases, telemetry, support behavior, and rollout strategy are named;
- dependencies and unresolved decisions have owners.

### A feature is done when

- acceptance and negative/permission cases pass in an integrated environment;
- domain, integration, contract, authorization, and critical-path E2E tests exist;
- schema migrations are reviewed and safe for the deployment sequence;
- authn/authz, privacy, audit, idempotency/concurrency, and error behavior are verified for the feature’s risk;
- frontend loading/empty/error/forbidden/retry/accessibility states are integrated with the agreed design;
- logs, metrics, traces, alerts, support/runbook notes, and user-facing documentation are updated;
- CI is green, dependencies/secrets are clean, and no release-blocking finding is open;
- the product owner can demonstrate the behavior and the accountable owner accepts residual risk.

## 12. Immediate next actions

1. Product owner supplies answers to the P0 discovery questions and names the first MVP journey.
2. Frontend developer supplies the design/handoff artifacts for that journey, including all non-happy states.
3. Engineering converts the answers into `docs/PRODUCT_BRIEF.md`, a prioritized backlog, a data classification, and the first threat model.
4. Engineering writes the initial ADRs and chooses the stack using the decision gate above.
5. Engineering bootstraps R2: source layout, reproducible tooling, CI, environments, migrations, observability, and secure configuration.
6. Backend and frontend agree the first OpenAPI contract and generated/mock data.
7. Engineering builds and demonstrates R4 before expanding the MVP.

The next implementation work should not be a broad framework scaffold or a collection of disconnected screens. It should be a reviewed, observable, testable vertical slice tied to a confirmed user outcome.

## 13. Research references

- [NIST Secure Software Development Framework](https://csrc.nist.gov/projects/ssdf)
- [NIST SP 800-63-4 Digital Identity Guidelines](https://csrc.nist.gov/pubs/sp/800/63/4/final)
- [OWASP Application Security Verification Standard 5.0.0](https://owasp.org/www-project-application-security-verification-standard/)
- [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
- [OWASP Business Logic Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Business_Logic_Security_Cheat_Sheet.html)
- [OWASP Transaction Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html)
- [OWASP Third-Party Payment Gateway Integration Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Third_Party_Payment_Gateway_Integration_Cheat_Sheet.html)
- [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [OpenAPI Specification 3.2.0](https://spec.openapis.org/oas/v3.2.0.html)
- [RFC 9457 — Problem Details for HTTP APIs](https://datatracker.ietf.org/doc/html/rfc9457)
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- [PCI DSS](https://www.pcisecuritystandards.org/standards/pci-dss/)
