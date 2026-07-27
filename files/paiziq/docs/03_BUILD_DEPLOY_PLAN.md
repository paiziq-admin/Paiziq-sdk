# Paiziq Agent Audit Tracer — Build & Deployment Plan

**Scope:** Backend SDK and its path to production, plus the planned hosted account/subscription program. The shipped dashboard frontend remains a companion workstream/repository consuming the contracts defined here.

---

## Phase 0 — Production Scaffold ✅ (delivered in this repo)

| Item | Status |
| --- | --- |
| `/sdk` Python package (src layout, `pyproject.toml`, entrypoint, importable locally) | ✅ |
| Decision engine: threshold, allowlist/blocklist, unknown merchant, budget, review-required, harmful intent | ✅ |
| Verdicts `approved`/`needs_review`/`rejected` with reasons + risk flags | ✅ |
| 4-Way Match audit (identity, intent, policy, transaction tamper check) | ✅ |
| `PaiziqSDK` API: `review_payment`, `execute_payment`, `get_audit_trail` (+ `approve_review`) | ✅ |
| Tracer + Console/InMemory/HTTP(batched, retrying) exporters → dashboard ingest contract | ✅ |
| Notifications: harmful intent (critical), rejected (warning), needs-review (info); webhook delivery | ✅ |
| Integrations: generic decorator, LangChain callback handler, OpenAI tool-call guard | ✅ |
| Test suite: 44 tests, all passing; happy-path example runs end-to-end | ✅ |

## Phase 1 — Production Hardening (Weeks 1–3)

**Week 1 · Quality gates & packaging**
- GitHub Actions CI: `ruff check`, `mypy`, `pytest --cov` (gate ≥ 90% on `engine/`), matrix Python 3.10–3.12.
- `python -m build` artifacts published to AWS CodeArtifact on tagged releases; CHANGELOG + semver policy.
- Property-based tests (hypothesis) on rule boundaries; concurrency tests on `BudgetTracker` and `HTTPExporter`.

**Week 2 · Production stores**
- `RedisBudgetStore` (sorted-set spend windows) implementing `BudgetStore` for multi-process fleets.
- `PostgresAuditStore` implementing `AuditStore` (append-only table, request_id + trace_id indexes).
- PII-scrubbing hook on span export (configurable redaction of `intent_description`/metadata).

**Week 3 · Ingest plane (minimum viable cloud)**
- FastAPI ingest service: `POST /v1/traces`, `POST /v1/notifications`, API-key auth, request size limits, idempotent span upsert.
- Terraform: ECS Fargate + ALB, SQS buffer, RDS Postgres, ElastiCache, Secrets Manager, CloudWatch alarms.
- Load test: 1k spans/s sustained per ingest task; SDK exporter drop-rate < 0.1% at target load.

**Exit criteria:** versioned package installable from CodeArtifact; traces from the example agent visible in the dashboard's trace explorer against the staging ingest plane; CI green on every PR.

## Phase 2 — Pilot Readiness (Weeks 4–6)

- SDK callback bridge: PZ-101 already surfaces `needs_review` decisions in the dashboard queue and resolves the control-plane payment atomically; the remaining work is delivering a signed resolution back to an SDK process's local `approve_review` state (or a short-poll fallback for serverless agents).
- `StripeGateway` (sandbox) implementing `PaymentGateway`; gateway conformance test kit.
- Server-side LLM Intent Judge: async Langfuse-pipeline evaluation of exported reasoning traces; verdicts annotate traces and can raise retroactive security alerts.
- Pilot: instrument one design partner's procurement agent (LangChain); success = 100% of payment attempts traced, zero unreviewed spend above threshold, < 5 ms p95 SDK overhead.

## Phase 3 — GA Track (Weeks 7–9)

- SD-JWT mandate signing/verification (Verifiable Intent alignment) layered onto the existing `Mandate` model.
- Mastercard Agent Pay sandbox connector; multi-gateway routing policy.
- OTLP exporter bridge; TypeScript SDK kickoff against the frozen v1 wire contract.
- SOC 2 evidence: audit-trail immutability controls, retention policy, access logging.

## Risk Register

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Framework API churn (LangChain) breaks the handler | High | Integrations isolated + lazily imported; contract tests pinned per minor version; generic decorator as universal fallback |
| Budget race in multi-process agents | Medium | Redis store with atomic ops (Phase 1); document in-memory store as dev-only |
| Heuristic harmful-intent misses sophisticated evasion | Medium | Defense in depth: server-side LLM judge + velocity/budget rules still bound the damage |
| Trace loss under network partition | Medium | Bounded queue + retry/backoff; drop counters alarmed; optional JSONL spool-to-disk fallback (Phase 1 backlog) |
| Customers fear latency on the payment path | Low | Deterministic rules only on hot path; publish p95 benchmarks in docs |

## Engineering Conventions

Python ≥ 3.10, `src/` layout, typed dataclasses, protocol-based seams (Rule, Exporter, BudgetStore, AuditStore, PaymentGateway, Notifier), zero core dependencies, ruff + mypy clean, every public behavior covered by a unit test, observability failures never raised into the host agent.

## Definition of Done (library GA)

1. All Phase 0–2 acceptance criteria green in CI.
2. Two framework integrations validated against live framework versions (LangChain, OpenAI SDK).
3. One pilot agent in production for 2 weeks with zero SDK-caused incidents.
4. Wire contract v1 frozen and documented; dashboard renders traces, decisions, 4-way results, and notifications end-to-end.

## Hosted Account and Subscription Commercialization Program

**Status:** Planned, not implemented. This program operationalizes the
draft commercial rules in `01_PRODUCT_SCOPE.md`, section 10, and the
target design in `02_ARCHITECTURE.md`, section 10. It does not change the
existing SDK GA sequence or imply that hosted billing is production-ready.

The sequence below is an estimate, not a date commitment. With two backend
engineers, one dashboard engineer, and part-time product, finance,
security/legal, support, and operations owners, the critical path is
approximately 13–15 weeks after commercial and provider decisions are
approved.

### Program invariants

1. Hosted dashboard/control-plane use requires OAuth/OIDC login, a user
   account, an active organization membership, and an internal
   subscription (Free included).
2. Machine-to-machine SDK and agent traffic continues to use scoped API
   keys. A browser session never reveals or substitutes an environment API
   key.
3. Organization and environment isolation is completed and tested before
   any unrelated paid customer is onboarded.
4. The internal subscription and entitlement revision—not a checkout
   redirect or raw provider status—controls access.
5. Every provider mutation and event is idempotent, verified, durable,
   retryable, auditable, and safe under duplicate or out-of-order delivery.
6. Subscription enforcement never makes a payment decision more
   permissive or disables the customer's local deterministic safety layer.
7. Billing records use a separate bounded context from existing
   agent-payment proposals.
8. Local, development, and production credentials, data, provider objects,
   webhook secrets, callback URLs, and queues are isolated.
9. No card number, CVC, OAuth client secret, refresh token, or live API key
   enters source, logs, browser storage, or Paiziq billing tables.
10. Every implementation increment ships migrations/code, tests, OpenAPI
    and generated types when applicable, CHANGELOG, tracker status, and a
    green `make check`.

**Weekly program cadence**

- Planning confirms milestones, dependencies, decision owners, and any
  pricing/policy question that would otherwise cause engineering rework.
- Execution review demonstrates completed customer and operator workflows,
  test evidence, failed gates, and current blockers.
- Operational review examines authentication/billing accuracy, provider
  events, reconciliation exceptions, usage drift, support cases, incidents,
  and production-readiness approvals.

### Stage C0 — Approval and P0 hardening (Weeks 1–2)

**Business and operating decisions**

- Assign accountable owners for product/pricing, finance/reconciliation,
  identity/security, legal/policy, support, engineering, operations, and
  launch.
- Approve or revise the draft plan names, prices, annual discount, trials,
  limits, overage, lifecycle timings, refunds, support targets, and
  retention.
- Select the OAuth/OIDC identity provider and initial login providers.
- Select a billing provider or merchant of record. Stripe Billing is the
  reference adapter only; it is not the existing agent-payment
  `StripeGateway`.
- Approve tax, invoice numbering, currencies/regions, cancellation,
  dispute, privacy, data-deletion, and customer-communication policies.
- Record every unresolved choice from product-scope section 10.6 as a
  launch-blocking decision with owner and due date.

**Security and deployment prerequisites**

- Convert all resource routes and stores to deny-by-default
  organization/environment scoping from `AuthContext`; add list, detail,
  mutation, filter, and resource-existence cross-tenant tests.
- Add/backfill ownership for spans, notifications, audit entries, and any
  globally queryable derived index. Define safe legacy-row handling.
- Restrict production bootstrap credentials to audited break-glass use.
- Resolve append-only audit retention through archive/partition design,
  not destructive updates or deletes.
- Repair and smoke-test the production container so every imported module,
  router, worker, migration, and SDK dependency is present.
- Choose the production persistence/queue topology and restore procedure;
  the current SQLite-only image and unbuilt Terraform target are not a
  paid-production deployment.

**Exit gate**

- Commercial decision record approved.
- Threat model and tenancy matrix reviewed.
- Cross-tenant backend test suite green.
- Production image boots, migrates a disposable database, passes health
  and one authenticated request, and shuts down cleanly.
- No hosted checkout or unrelated beta tenant proceeds before this gate.

### Stage C1 — Human identity, account, and membership (Weeks 3–4)

**Backend**

- Add transactional migrations and injected stores for users, OIDC
  identities, sessions, memberships, invitations, and billing accounts.
- Implement Authorization Code + PKCE, state/nonce/issuer validation,
  opaque server sessions, CSRF protection, logout/revocation, idle and
  absolute expiry, and secure production cookie policy.
- Add a session-aware authentication context carrying user, organization,
  membership role, and selected environment.
- Define organization roles at minimum: owner, admin, billing admin,
  developer, reviewer, and viewer. Keep internal operator roles separate.
- Audit login, logout, session revocation, invitations, membership/role
  changes, organization switching, and billing-contact changes without
  logging tokens or claims containing unnecessary PII.

**Dashboard**

- Replace arbitrary backend URL/API-key login with trusted runtime API
  configuration, OAuth start/callback, session bootstrap, safe return-path
  validation, logout, and account recovery/error states.
- Remove the production demo-session bypass. Keep a deterministic fake
  identity only in test builds that cannot start as production.
- Split authentication, selected organization/environment, permissions,
  and entitlements into distinct state boundaries.
- Add user menu, profile/session view, organization selection, members,
  invitations, and role management.
- Keep API-key create/rotate/revoke under authenticated organization
  settings for machine identities; never persist those secrets.

**Exit gate**

- A new user can log in, create or join an organization, log out, revoke
  sessions, and switch only among authorized organizations.
- Account creation precedes any checkout.
- OAuth denial, callback replay, changed email, duplicate linking,
  invitation replay/expiry, session fixation, expiry, CSRF, and
  cross-tenant tests pass.
- Every dashboard operator route redirects anonymous users and restores a
  validated intended path after login.

### Stage C2 — Catalog, entitlements, and usage (Weeks 5–6)

- Add versioned feature, plan, price, and plan-entitlement stores seeded
  from the approved matrix. Provider IDs remain environment configuration.
- Create one internal Free subscription when an organization is created
  and one compiled entitlement revision for every subscription state.
- Implement one centralized access-decision service returning distinct
  permission, entitlement, status, and limit outcomes.
- Inventory every existing route/action and attach its stable catalog key.
  Enforce on server mutations and reads; dashboard route/button treatment
  is presentation only.
- Add append-only, idempotent protected-payment usage events and rebuildable
  counters. Count the first decision for a unique payment, not retries or
  re-evaluations.
- Add atomic checks/reservations for members, environments, active agents,
  API keys, webhook endpoints, and protected-payment volume.
- Implement 80%/100% usage notices, overage-buffer behavior, no-rollover
  reset, scheduled reset, and counter reconciliation.
- Add entitlement snapshots to the authenticated dashboard bootstrap and
  usage meters to organization settings.

**Exit gate**

- The approved plan matrix can be represented without code branches per
  plan.
- Boolean, numerical, override, expired grant, concurrent-limit, cache
  invalidation, billing-boundary, no-rollover, and usage-rebuild tests pass.
- Every restricted backend operation rejects an unentitled direct request,
  even if the dashboard is bypassed.
- A quota/billing failure cannot yield `approved`.

### Stage C3 — Billing adapter, checkout, and event ledger (Weeks 7–9)

- Define an injected `BillingProvider` boundary for customers, hosted
  checkout/portal, subscriptions, invoices, refunds, and current-object
  reconciliation. Keep provider SDK imports out of the zero-dependency SDK
  core.
- Add billing tables, immutable subscription transitions, checkout
  references, provider event ledger, processing exceptions, and
  transactional outbox.
- Build account-first checkout for plan, interval, currency, promotion,
  eligibility, and exact charge preview. Prevent duplicate submissions
  with an internal checkout reference plus idempotency keys.
- Accept provider events only after raw-body signature verification,
  durable storage, and uniqueness checks. Acknowledge quickly; process on
  a worker with retry/backoff and a dead-letter/exception queue.
- Reconcile duplicate, delayed, and out-of-order events against the current
  provider object before applying lifecycle changes.
- Make the return page read/poll internal checkout/subscription state.
  Payment-provider redirects and client parameters never activate access.
- Generate notification/outbox work for confirmation, receipt, payment
  failure, action required, trial ending, renewal, cancellation, refund,
  dispute, and payment-method expiry.
- Validate hosted checkout so Paiziq stores provider references only and
  does not handle raw card data.

**Exit gate**

- Free, trial, monthly, and annual subscriptions activate only after the
  correct internal evidence.
- Refresh, double-click, network timeout, abandoned checkout, delayed
  approval, duplicate events, worker crash/restart, event reordering, and
  partial local failure are safe to retry without duplicate subscriptions
  or charges.
- Provider sandbox ledger, internal invoice/payment facts, subscription
  transition, entitlement revision, audit record, and customer
  confirmation reconcile for every successful checkout.

### Stage C4 — Lifecycle and self-service operations (Weeks 10–11)

**Customer billing area**

- Show current plan/version, internal status, trial/renewal/access dates,
  interval, billing contact, provider-managed payment method, pending plan
  change, usage, exact upcoming charge, company/tax identity, and invoice
  address.
- Provide hosted payment-method management, invoice history/download,
  immediate upgrade, scheduled downgrade, period-end cancellation,
  reactivation, and clear failure recovery.
- Explain excess members, agents, environments, API keys, webhooks,
  retained data, and feature loss before downgrade.
- Distinguish cancel at period end from exceptional immediate cancellation;
  pause remains unavailable until an approved policy exists.

**Lifecycle operations**

- Implement renewal, retry cadence, past-due/grace/suspension deadlines,
  trial conversion/expiry, upgrade proration, scheduled downgrade,
  cancellation, expiration-to-Free, reactivation, refunds, disputes,
  promotions, and temporary access grants.
- Schedule time-driven transitions durably; jobs are idempotent and
  re-check the current state/period before acting.
- Preserve existing verified access during provider outages. Restrict
  suspended accounts without deleting data or weakening SDK enforcement.
- Send distinct, time-specific notices rather than one generic failure
  message.

**Internal administration**

- Provide separate support, billing-operations, finance, product-operations,
  and administrator permissions.
- Support account/subscription/invoice/event history search, exception
  replay, temporary access, approved discounts, cancel/reactivate, usage
  review, and account notes.
- Require a reason, operator identity, timestamp, correlation ID, and
  before/after values for sensitive actions. Define two-person approval
  thresholds for large refunds, long temporary grants, and custom
  entitlements.
- Record every customer- and operator-initiated billing mutation with the
  effective date and provider/internal correlation references.

**Exit gate**

- All lifecycle transitions are deterministic, documented, audited, and
  recoverable.
- Dashboard dates and charges match internal records and provider sandbox.
- A downgrade never deletes customer data; an unresolved over-limit state
  blocks only the documented new activity.

### Stage C5 — Finance, support, metrics, and production operations (Weeks 12–13)

**Daily reconciliation**

```text
Provider customers/subscriptions/invoices/payments/refunds/disputes
  ↓
Normalize by provider reference and currency
  ↓
Match internal billing account, invoice, subscription, and access period
  ↓
Match processor fees and settlement
  ↓
Open assigned exception with evidence
  ↓
Resolve, replay if needed, and record the adjustment/reason
```

At minimum, report payment without subscription, active paid access
without payment/trial/grant, duplicate invoice, missing refund, settlement
mismatch, unknown customer, currency/amount mismatch, stale entitlement
revision, and unprocessed event backlog.

The daily finance view tracks gross collections, recurring and promotional
discounts, taxes, processor fees, refunds, disputes/chargebacks, credits,
net settlements, outstanding invoices, and failed collection attempts in
original currency plus the approved reporting-currency treatment.

**Initial metric specification**

| Metric | Formal definition | Source of truth | Owner / refresh |
| --- | --- | --- | --- |
| MRR | Active recurring lines normalized to one month, net recurring discounts; exclude tax, fees, one-time charges, and refunded recurring value | Internal invoice/subscription ledger reconciled to provider | Finance / daily |
| ARR | `12 × MRR`; not signed contract value | MRR result | Finance / daily |
| New subscriptions | First transition of a billing account into a paid active plan in period | Subscription transitions | Product/Finance / daily |
| Renewals | Paid invoice extending an existing paid access period | Billing invoices + transitions | Finance / daily |
| Upgrades/downgrades/cancellations | Count of effective plan-direction or cancellation transitions, not clicks | Subscription transitions | Product / daily |
| Logo churn | Paid billing accounts that expire in period ÷ paid accounts active at period start | Subscription transitions | Product/Finance / monthly |
| Trial conversion | Trial billing accounts entering paid active within 30 days of trial end ÷ trials ending in cohort | Subscription transitions | Product / daily cohort |
| Average revenue per paid customer | MRR ÷ active paid billing accounts; calculate per reporting currency policy | MRR + active subscription population | Finance / daily |
| Payment recovery | Past-due billing accounts returning active before suspension/expiry ÷ past-due accounts | Subscription transitions + invoices | Billing operations / daily |
| Failed payment rate | Failed collection attempts ÷ all collection attempts, grouped by first attempt versus retry | Billing payments/provider events | Billing operations / daily |
| Refund rate and value | Verified refund count and amount ÷ successful charge count and gross collections | Billing refunds/payments | Finance / daily |
| Active users by plan | Distinct active members with a valid session/activity event in period, grouped by plan version | Membership/activity + entitlement snapshot | Product / daily |
| Feature usage by plan | Distinct successful access-decision/action events grouped by feature key and plan version | Access-decision events | Product / daily |
| Limit utilization | Usage counter ÷ effective numeric entitlement, by meter and plan | Usage events/counters | Product operations / hourly |
| Accounts approaching/exceeding limits | Distinct accounts at ≥80% and ≥100%, plus repeat exceeders in the prior three cycles | Usage counters/history | Product operations / hourly |
| Entitlement-check failures | Denials grouped by feature, reason, plan version, and environment; exclude synthetic accounts | Access-decision events | Engineering/Product / hourly |
| Activation failures | Verified paid checkout not active within five minutes | Provider events + checkout + subscription | Operations / five minutes |

Every metric explicitly excludes tagged synthetic/test accounts. Refunds,
discounts, paused states, credits, currencies, and plan migrations require
documented treatment before finance signs off.

**Alerts**

- OAuth callback/session error rate, suspicious login/recovery events, and
  administrative privilege changes.
- Checkout failure/abandonment spikes and paid-but-not-active accounts.
- Provider signature failures, processing lag, retries, dead letters, and
  oldest unprocessed event age.
- Failed renewals, grace/suspension volume, subscription/entitlement
  mismatch, invoice/finalization failure, and duplicate subscription risk.
- Usage-counter drift, entitlement-denial spikes, unexpected cancellation,
  refund/dispute spikes, and reconciliation exceptions.

**Operational deliverables**

- Runbooks for authentication outage, provider outage, event backlog,
  incorrect entitlement, duplicate charge, failed renewal, refund,
  dispute, and reconciliation mismatch.
- Customer communication templates for trial, renewal, failure retries,
  suspension, cancellation, refund, dispute, incident, and recovery.
- Published terms, privacy, refund/cancellation, data retention/export, and
  tax policies; support escalation and approval thresholds.
- Production backups/restores, queue recovery, secret rotation, rate
  limits, dashboards, alerts, on-call ownership, and incident review.

An incident follows one controlled workflow: confirm customer impact,
assign severity/owner, stop further damage without mass-revoking valid
access, restore processing, reconcile every affected account, notify
customers when required, document root cause/timeline, and track preventive
actions to verification.

### Stage C6 — Complete scenario validation and controlled launch (Weeks 14–15)

Run each business scenario in both provider sandbox automation and an
operator-observed end-to-end environment:

1. New monthly subscription.
2. New annual subscription.
3. Free trial conversion.
4. Trial expiration without payment.
5. Successful renewal.
6. Failed renewal.
7. Payment recovery.
8. Immediate upgrade with proration.
9. Scheduled downgrade while above lower limits.
10. Cancellation at period end.
11. Exceptional immediate cancellation.
12. Reactivation before and after expiration.
13. Full refund.
14. Partial refund.
15. Charge dispute.
16. Duplicate provider event and duplicate checkout submission.
17. Delayed/out-of-order provider event and failed return page.
18. Account deletion with retention/legal-hold rules.
19. Every numerical plan limit exceeded concurrently.
20. Administrative subscription/entitlement change.

For every scenario verify customer status and dates, internal subscription
and transition, feature/permission/limit access, invoice/payment/refund
facts, usage reset/counter, notification/outbox result, append-only audit,
reconciliation, and reporting.

Additional identity/security validation covers OAuth denial and provider
outage, callback replay, state/nonce/PKCE failure, account linking, changed
email, invitation expiry/replay, session fixation/expiry/revocation, CSRF,
open redirect, organization switching, and cross-tenant list/detail/mutation
attempts.

**Launch phases**

1. **Internal:** employee organizations; inspect every auth and billing
   transition manually.
2. **Limited beta:** a small invited cohort using live low-volume payments;
   reconcile every transaction daily and provide manual support.
3. **Controlled launch:** percentage of new eligible organizations; gate on
   activation, failure, reconciliation, churn, and support thresholds.
4. **General availability:** all eligible customers only after operational
   metrics remain within approved limits and every acceptance criterion is
   signed.

Launch day includes a real low-value purchase and refund, OAuth login,
verified event processing, entitlement activation, invoice/notification,
reconciliation, rollback check, and on-call confirmation before traffic is
enabled.

### Deployment profiles

Application deployment stage and customer data-plane environment are
different axes. `local | development | production` identifies where Paiziq
runs. Existing customer environments remain `sandbox | production`; adding
a customer `development` kind requires a separate product decision.

| Concern | Local | Development | Production |
| --- | --- | --- | --- |
| Runtime stage | Explicit `local` profile; production validation impossible | Isolated `development` profile | Fail-fast `production` profile |
| Dashboard/API | Fixed localhost origins through dev proxy | Fixed nonproduction HTTPS domains | Fixed production HTTPS domains, HSTS/CSP |
| OAuth/OIDC | Dedicated localhost client or containerized test issuer; real flow by default | Separate nonproduction tenant/client and exact callbacks | Separate live tenant/client; no test users or callback wildcards |
| Automated auth tests | Deterministic fake issuer allowed only under test mode | Test identities, no customer data | Fake issuer/test bypass rejected at startup |
| Data | Disposable SQLite or local Postgres with fixtures | Durable isolated Postgres/queue, synthetic accounts | Managed Postgres/queue/workers, backups, restore drills, residency policy |
| Billing | Fake adapter or provider test mode; provider CLI may forward signed events | Separate provider test account/objects/webhook secret | Live provider account/objects/webhook secret; least privilege |
| Email/notifications | Local sink; no external delivery by default | Sandbox recipients/domain | Approved sending domain, suppression and incident controls |
| Secrets | Uncommitted local secret store | Nonproduction secret manager/KMS | Production secret manager/KMS, rotation and access audit |
| Reconciliation | Deterministic fixture ledger | Test-provider daily job | Daily finance reconciliation plus settlement exceptions |
| Data mixing | Never copy production customer records | Synthetic or explicitly scrubbed fixtures only | Production customer data only |

No OAuth client secret or provider secret is exposed through dashboard
build variables. Separate client IDs, callback URLs, provider product/price
IDs, webhook secrets, databases, queues, encryption keys, and email domains
are mandatory.

### Hosted production acceptance criteria

Hosted subscriptions may be called production-ready only when:

1. Product, finance, legal, security, support, engineering, and operations
   approve the current plan matrix and policies.
2. Every human user logs in through OAuth/OIDC and belongs to the
   organization being accessed; production demo/API-key browser login is
   absent.
3. Cross-tenant checks are exhaustive and spans, notifications, audit,
   search, and metrics carry enforceable ownership.
4. Each plan version has approved prices, feature entitlements, numerical
   limits, reset/rollover behavior, support, and retention.
5. All access decisions use Paiziq's internal subscription/entitlement
   state after verified provider-event processing.
6. Checkout, provider mutations, usage, scheduled jobs, and event handling
   are safe under retries, duplicates, crashes, and reordering.
7. Upgrades, downgrades, renewals, failures, cancellations, refunds,
   disputes, expiration, and reactivation match documented dates and
   access.
8. Billing or quota failures never make an agent payment more permissive
   or remotely disable local deterministic safeguards.
9. Customers can manage common billing actions and understand exact
   charges/effective dates without support.
10. Support/finance can investigate, replay, reconcile, refund, and grant
    temporary access under distinct audited permissions.
11. Every provider payment, invoice, refund, fee, and settlement
    reconciles or has an owned exception.
12. Production image, migrations, database, queues/workers, backups,
    restores, monitoring, alerts, rate limits, secret rotation, and
    incident runbooks have current evidence.
13. Legal/customer policies are published and consent records retained.
14. All identity, tenancy, entitlement, lifecycle, and 20 business scenario
    gates are green in CI and the production-like environment.
15. Limited beta completes with no unresolved severity-1/2 issue and
    approved activation, event-lag, reconciliation, and support thresholds.
