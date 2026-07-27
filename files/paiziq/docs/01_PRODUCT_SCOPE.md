# Paiziq Agent Audit Tracer — Product Scope & Technical Acceptance Criteria

**Version:** 1.1 · **Date:** July 2026 · **Scope:** Shipped backend SDK/control plane plus hosted commercialization planning

---

## 1. Problem Statement

When AI agents execute payments autonomously, three questions go unanswered: did the principal authorize the transaction, did the agent follow instructions exactly, and can anyone prove what happened afterward? Enterprises deploying payment agents on LangChain, the OpenAI SDK, and similar frameworks have no standard layer that audits agent intent before money moves. The cost of not solving this is direct financial loss from runaway or manipulated agents, plus an accountability gap that blocks enterprise adoption of agentic commerce.

## 2. Product Definition

The **Paiziq Agent Audit Tracer** is a Python SDK that developers install on top of their existing agent stack. It intercepts payment actions, evaluates them against a declarative policy through a deterministic decision engine, enforces a **4-Way Match audit** (Identity, Intent, Policy, Transaction) before execution, notifies the user on harmful intent, and streams every trace to the **Paiziq admin dashboard** for security audit and review workflows.

It is a tracer-style library in the spirit of Langfuse/OpenTelemetry — but instead of only observing, it **decides and enforces**.

## 3. Goals

1. A developer can instrument an existing payment agent in under 15 minutes with fewer than 10 lines of code.
2. 100% of agent payment attempts produce an explainable verdict (`approved` / `needs_review` / `rejected`) with reasons and risk flags.
3. Zero payments execute without passing the 4-Way Match audit; tampering between review and execution is detected.
4. Harmful or evasive agent intent triggers a user notification within seconds of detection.
5. Every review, audit verdict, and execution is visible in the Paiziq admin dashboard as a trace with a complete, immutable audit trail.

## 4. Non-Goals (v1)

- **Building the dashboard frontend in this repository** — the shipped React dashboard is maintained as a companion workstream/repository and consumes this backend's contract.
- **Real payment-gateway certification** — v1 ships a gateway abstraction with a mock/sandbox implementation; Stripe/Mastercard Agent Pay connectors are fast-follows.
- **LLM-as-judge semantic intent analysis in the SDK** — the SDK runs fast deterministic/heuristic checks locally; deep semantic judging happens server-side (Langfuse-style pipeline) where latency is acceptable.
- **Cryptographic SD-JWT intent tokens** — the `Mandate` model is designed to carry SD-JWT claims later, but signing/verification is deferred to v2 to keep v1 dependency-free.
- **Non-Python runtimes** — TypeScript SDK is a roadmap item; the wire protocol is language-neutral JSON to enable it.

## 5. Target Users & User Stories

- As an **AI engineer**, I want to wrap my agent's payment tool with one decorator so that every payment is reviewed without rewriting my orchestration code.
- As a **platform/security engineer**, I want declarative policies (thresholds, merchant lists, budgets) so that controls live in configuration, not in prompts.
- As a **finance owner (CFO/controller)**, I want payments above a threshold held for my approval, and I want an audit trail proving what the agent intended versus what it did.
- As an **end user/principal**, I want an immediate notification when my agent shows harmful intent or a payment is blocked, so I stay in control.

## 6. Scope of Work (Workstreams)

### WS-1 · SDK package initialization (`/sdk`)
Python package structure under `src/` layout, `pyproject.toml`, package entrypoint (`paiziq.__init__`), pytest test structure.

**Acceptance criteria**
- [x] SDK can be imported locally (`pip install -e . && python -c "import paiziq"`)
- [x] Project structure is clean and runnable (src layout, typed dataclasses, no circular imports)
- [x] Unit test setup exists (`pytest` configured in pyproject, `tests/` collects and runs)

### WS-2 · Payment decision rules in the SDK
Move all decision logic into the SDK's `DecisionEngine` with independent, composable rules: threshold checks, merchant allowlist/blocklist, unknown-merchant detection, budget validation (daily/monthly, warning ratio), and review-required logic (categories, currency, velocity).

**Acceptance criteria**
- [x] Engine returns exactly one of `approved`, `needs_review`, `rejected` (severity: rejected > needs_review > approved)
- [x] Reasons (human-readable) and risk flags (machine-readable enum) are included on every decision, preserving findings from all rules — not just the deciding one
- [x] Rules pass unit tests (each rule tested in isolation: happy path, boundary, and negative cases; 44 tests green)
- [x] Custom rules can be registered via the `Rule` protocol without forking the SDK

### WS-3 · Developer-facing API
`PaiziqSDK` facade exposing `review_payment(...)`, `execute_payment(...)`, `get_audit_trail(...)` (plus `approve_review(...)` for the human-in-the-loop path).

**Acceptance criteria**
- [x] API is simple enough to use from a test agent (plain dataclasses in/out; the happy-path example is ~30 lines)
- [x] No framework-specific concepts leak into the interface (LangChain/OpenAI live only in `tracing/integrations`, imported lazily)
- [x] Happy-path example works end-to-end (`examples/happy_path.py`: review → 4-way audit → execution → audit trail)

### WS-4 · 4-Way Match audit policy
Pre-execution verification: (1) Identity — principal/agent match the mandate; (2) Intent — transaction within mandate bounds (amount, merchant scope, currency, expiry); (3) Policy — decision-engine verdict permits execution; (4) Transaction — payload identical to what was reviewed (tamper detection via canonical snapshot).

**Acceptance criteria**
- [x] A payment executes only if all four dimensions pass
- [x] Each dimension reports pass/fail with a detail string, recorded on the trace and audit trail
- [x] Post-review payload tampering is detected and blocks execution

### WS-5 · Tracing & dashboard integration
Dependency-free tracer (spans, trace context, nesting) with pluggable exporters; batched `HTTPExporter` ships spans to the Paiziq dashboard ingest API (`POST /v1/traces`, Bearer auth) with retry/backoff on a daemon thread.

**Acceptance criteria**
- [x] Every `review_payment`/`execute_payment` call emits spans carrying verdict, risk flags, and 4-way results
- [x] Export is asynchronous and lossy-by-design under backpressure — observability never blocks or crashes the agent
- [x] Exporter failures are logged, never raised into the host application

### WS-6 · Notifications & security audit
Notification router maps decisions to alerts: harmful intent → critical, rejection → warning, needs-review → info. `WebhookNotifier` delivers to the Paiziq notification service (Slack/email fan-out server-side). Append-only audit stores (in-memory, JSONL; Postgres via protocol).

**Acceptance criteria**
- [x] Harmful-intent detection fires a critical notification naming the agent, merchant, amount, and reasons
- [x] Audit trail is append-only and queryable per request, with trace correlation IDs
- [x] Notification delivery failure never interrupts the payment flow

### WS-7 · Framework compatibility
Three integration styles funneling into one SDK: a generic decorator for any framework, a LangChain `BaseCallbackHandler`, and an OpenAI tool-call guard. Extras-based installs (`paiziq[langchain]`, `paiziq[openai]`); core stays zero-dependency.

**Acceptance criteria**
- [x] Core `pip install paiziq` pulls zero third-party packages (no version conflicts with host frameworks)
- [x] Blocked payments raise `PaymentBlockedError` carrying the full `Decision` so agent loops can self-correct
- [x] Integrations import their framework lazily and fail with actionable install guidance

## 7. Success Metrics

**Leading:** time-to-first-trace for a new integration (< 15 min target); % of payment attempts with a verdict (100%); SDK overhead per review (< 5 ms p95, deterministic rules only); trace delivery success rate (> 99.5% with retries).
**Lagging:** blocked-loss value surfaced in the dashboard; design-partner adoption (3 pilot integrations in Q3); reduction in unreviewed agent spend at pilot accounts (> 90%).

## 8. Open Questions

- **(Product/Security)** The PZ-101 dashboard/control-plane queue and atomic payment resolution are shipped. A separate callback bridge that feeds a signed control-plane resolution back into an SDK process's local `approve_review` state remains a later integration decision.
- **(Engineering)** Budget state in multi-process agent fleets requires the Redis `BudgetStore`; confirm pilot deployment topology.
- **(Legal/Compliance)** Audit-record retention period and PII handling in `intent_description` for SOC 2 scope.

## 9. Timeline & Phasing

- **Phase 0 (done — this scaffold):** package, decision engine, 4-way audit, tracer + exporters, notifications, integrations, tests, examples.
- **Phase 1 (Weeks 1–3):** dashboard ingest API hardening, Redis budget store, Postgres audit store, CI/CD, packaging to a private index.
- **Phase 2 (Weeks 4–6):** dashboard review round-trip, Stripe sandbox gateway, server-side LLM intent judge, pilot integration with first design partner.
- **Phase 3 (Weeks 7–9):** SD-JWT mandate signing, Mastercard Agent Pay sandbox, TypeScript SDK kickoff.

## 10. Hosted Account and Subscription Model (Planning Baseline)

**Status:** Draft for product, finance, legal, support, and engineering
approval. This section is the commercial planning source of truth; it is
not a claim that account, OAuth, billing, or entitlement functionality is
already implemented. Values must not be copied to customer-facing pricing
until the approval gates in section 10.6 are complete.

### 10.1 Customer, account, and billing unit

- A person must sign in and have a Paiziq user account to use the hosted
  dashboard or control plane. Public pricing, legal, status, documentation,
  and authentication entry/callback pages remain public.
- The **paying customer is an organization**. An individual receives a
  one-member organization, so individual and team accounts use the same
  tenancy, entitlement, and billing paths.
- One billing account and one current subscription belong to one
  organization for the initial release. Enterprise account hierarchies may
  group organizations later, but are not an initial-release promise.
- Human users access an organization through membership and roles. SDKs,
  agents, CI, and automation continue to use environment-bound API keys.
  Human roles, API-key permissions, subscription entitlements, and usage
  limits are independent checks.
- The recommended billing unit is a **flat organization fee with included
  members, environments, active agents, and protected-payment volume**.
  Contracted metered overage is reserved for Business and Enterprise after
  usage accuracy is proven.
- A protected-payment usage unit is the first persisted decision for one
  unique Paiziq payment ID. Retries, idempotent replays, human-review
  actions, and re-evaluations of that same payment do not create another
  billable unit.
- Usage resets at the subscription billing-cycle boundary in UTC and never
  rolls over. Calendar-month accounting is not used.
- All organizations, including Free organizations, have an internal
  subscription record. Free subscriptions have no payment-provider
  customer or price requirement.
- Plan versions and their entitlements are immutable after use. Existing
  subscriptions remain pinned to their version until an explicit migration
  or plan change is recorded.

The local, deterministic SDK safety layer is not remotely disabled by a
subscription state. A limit or billing failure must never turn a rejected
or `needs_review` payment into an approval. Hosted access and capacity are
commercially controlled; local enforcement remains fail-safe.

### 10.2 Feature and entitlement catalog

Boolean access and numerical limits are separate catalog entries. Existing
API-key scopes still authorize *who may perform an action*; the following
catalog controls *whether the organization purchased the capability*.
Every restricted mutation must enforce both on the server.

| Catalog key | Type | Product meaning | Delivery status |
| --- | --- | --- | --- |
| `sdk.enforcement` | baseline | Deterministic payment rules, explainable verdicts, and safe failure modes | Shipped; all plans |
| `sdk.four_way_audit` | baseline | Identity, Intent, Policy, and Transaction verification | Shipped; all plans |
| `sdk.integrations` | baseline | Generic, LangChain, OpenAI, exporter, and CLI integrations | Shipped; all plans |
| `identity.oauth_login` | boolean | OAuth/OIDC login and server-managed browser session | Required foundation |
| `identity.memberships` | boolean | Organization membership, invitations, and human roles | Required foundation |
| `cloud.sandbox` | boolean | Hosted control-plane use in sandbox environments | Shipped capability; gating required |
| `cloud.production` | boolean | Hosted control-plane use in production environments | Shipped capability; gating required |
| `cloud.ingestion` | boolean | Trace and notification ingestion/readback | Shipped capability; gating required |
| `payments.control` | boolean | Payment proposals, immutable decisions, and state history | Shipped capability; gating required |
| `payments.trace_read` | boolean | Payment detail, trace tree, reasons, and risk flags | Shipped capability; gating required |
| `policies.core` | boolean | Active policy enforcement and standard policy management | Shipped capability; gating required |
| `policies.governance` | boolean | Version compare, rollback, and simulation | Shipped capability; gating required |
| `reviews.core` | boolean | Review queue plus approve/decline with required notes | Shipped capability; gating required |
| `reviews.collaborative` | boolean | Claim, release, reassign, request information, escalation, and SLA fields | Shipped capability; gating required |
| `automation.webhooks` | boolean + limit | Signed endpoints, retry/DLQ, and delivery history | Shipped capability; gating required |
| `analytics.metrics` | boolean | Operational summary and time-series metrics | Shipped capability; gating required |
| `analytics.search` | boolean | Cross-event search | Shipped capability; gating required |
| `governance.audit_read` | boolean | Append-only audit-log visibility | Shipped capability; gating required |
| `governance.audit_export` | boolean | Customer export of audit records | Dashboard capability; server export contract required |
| `governance.retention` | value | Trace/notification history window and approved custom retention | Partial; enforcement hardening required |
| `tenant.members` | limit | Active human organization members; pending invitations do not count | Required foundation |
| `tenant.sandbox_environments` | limit | Active sandbox environments | Meter/gate required |
| `tenant.production_environments` | limit | Active production environments | Meter/gate required |
| `tenant.active_agents` | limit | Agents whose status is `active` | Meter/gate required |
| `tenant.active_api_keys` | limit | Non-revoked API keys | Meter/gate required |
| `usage.payment_evaluations` | limit | Unique protected payments per billing cycle | Required foundation |
| `tenant.webhook_endpoints` | limit | Enabled webhook endpoints | Meter/gate required |
| `billing.self_service` | boolean | Checkout, plan management, invoices, cancellation, and reactivation | Required foundation |
| `identity.enterprise_sso` | boolean | Customer-managed SAML/OIDC federation and lifecycle provisioning | Future; Enterprise must not promise it before delivery |
| `support.level` | value | Community, standard, priority, business, or contractual support | Operational foundation |

Platform security controls—tenant isolation, encryption, signature
verification, idempotency, audit integrity, rate limiting, session
security, and backups—are service baselines, never optional paid
features.

Numerical entitlements use these rules:

| Limit | Reset | Rollover | Behavior at the limit |
| --- | --- | --- | --- |
| Members, environments, active agents, API keys, webhook endpoints | None | Not applicable | Reject the create/reactivate action; preserve existing resources and data |
| Protected-payment evaluations | Billing cycle | No | Notify at 80% and 100%; allow a time-boxed recovery buffer; never produce a more permissive verdict |
| Trace/notification history | Continuous retention window | No | Expire eligible verbose history; never rewrite append-only audit records |
| Trial allowance | Once per billing account | No | Fall back to Free when the trial ends without a verified paid subscription |

At 100% of protected-payment volume, self-service plans enter a soft
overage buffer through the earlier of seven days or 120% usage. During
the buffer Paiziq continues safety-critical decision/audit handling and
prompts an upgrade. After the buffer, verbose trace ingestion and
nonessential paid mutations may be restricted; server-dependent payment
paths must return a structured limit failure that the SDK maps to
`needs_review` or fail-closed behavior. Business/Enterprise automatic
overage requires an explicit contract. No silent overage charge is
allowed during beta.

### 10.3 Draft Subscription Plan Matrix

Prices are working USD planning assumptions, exclude tax, and require
cost, market, and legal validation. Annual pricing equals ten monthly
payments (two months free). Trace/event storage is sold as a retention
window rather than an invented GB allowance; byte-based storage pricing
may be added only after storage metering exists. Security audit metadata
retains the platform minimum of at least 365 days, independent of the
customer-visible trace window.

| Plan | Free | Basic | Professional | Business | Enterprise |
| --- | --- | --- | --- | --- | --- |
| Intended customer | Evaluation or occasional user | Individual builder or very small team | Power user or small platform/security team | Multi-team organization | Large or regulated customer |
| Monthly price | $0 | $49 | $249 | $999 | Custom |
| Annual price | $0 | $490 | $2,490 | $9,990 | Annual contract |
| Trial | None | 14 days | 14 days | 30-day assisted trial | Negotiated pilot |
| Active members | 1 | 3 | 10 | 50 | Custom |
| Sandbox environments | 1 | 2 | 5 | 20 | Custom |
| Production environments | 0 | 1 | 3 | 10 | Custom |
| Active agents | 2 | 10 | 50 | 250 | Custom |
| Active API keys | 2 | 10 | 50 | 250 | Custom |
| Protected payments / billing cycle | 500, sandbox only | 10,000 | 100,000 | 1,000,000 | Contracted |
| Webhook endpoints | 1 sandbox | 2 | 10 | 50 | Custom |
| Customer-visible trace/notification history | 7 days | 30 days | 90 days | 365 days | Custom, audit minimum respected |
| Hosted production control plane | No | Core | Full | Full | Full/custom |
| Policy governance and simulation | Sandbox evaluation | Core policies | Full | Full | Full/custom |
| Human review | Sandbox evaluation | Core, up to 3 members | Collaborative | Collaborative + operational governance | Custom scale |
| Metrics and search | Basic sandbox | Basic | Full | Full | Full/custom |
| Audit access/export | Read in sandbox | Read | Read + export | Read + export + retention controls | Custom |
| Enterprise federation | No | No | No | No | Planned; contract only after shipped |
| Support | Community | Standard email, two-business-day target | Priority, one-business-day target | Business, eight-business-hour target | Contractual SLA |

All paid trials use the selected plan's features and limits and are
available once per billing account. The default proposal is no payment
method required for Basic/Professional trials; Business/Enterprise
trials are manually approved. Trial conversion and abuse controls must
be measured before this policy is finalized.

### 10.4 Internal subscription lifecycle

Provider status is evidence, not access control. Only a verified,
idempotently processed provider event or an authorized internal action
may transition the internal subscription. Access decisions read the
internal subscription and compiled entitlement revision.

| Internal state | Product access | Required product/operations behavior |
| --- | --- | --- |
| `incomplete_signup` | Public pages and account completion only | Resume OAuth/account setup; do not create paid access |
| `pending_payment` | Existing Free/trial access only | Show pending verification; poll internal status; never trust a redirect query parameter |
| `trial` | Trial plan entitlements | Show exact expiration and conversion choice; notify seven, three, and one day before expiry |
| `active` | Current plan entitlements | Normal access, metering, renewal, invoices, and usage notices |
| `past_due` | Current entitlements for up to 48 hours | Notify immediately; request payment update; retry safely |
| `grace_period` | Core safety and read access; restrict new members, environments, keys, and nonessential exports | Runs on days 3–7 after failure; retry and warn before suspension |
| `suspended` | Existing data read/export where allowed; no new paid mutations; safety paths remain fail-closed/review-required | Require successful payment or approved temporary grant; do not delete data |
| `paused` | Reserved; not offered at initial self-service launch | Requires a later approved policy before use |
| `canceled` | Current plan through `access_until`/period end | Automatic renewal disabled; show effective end date and reactivation |
| `expired` | Free entitlements if eligible, otherwise account/billing and retained-data access only | Offer reactivation; enforce retention/export policy |

`cancel_at_period_end` and a pending plan change are scheduled attributes,
not additional access-authority states. Invoice/payment conditions
(`pending`, `paid`, `failed`, `refunded`, `disputed`, `uncollectible`)
remain separate from subscription state.

The initial failed-renewal cadence is three collection attempts on days
0, 2, and 5, subject to provider capabilities. Notifications are sent
immediately, after the second attempt, before day-8 suspension, and after
suspension. Unresolved subscriptions remain suspended until day 30, then
become canceled/expired according to paid-through dates. A payment-provider
outage preserves previously verified access and queues reconciliation; it
does not mass-suspend customers.

### 10.5 Plan changes, cancellation, refunds, and excess usage

- **Account before payment:** OAuth account and organization creation occur
  before checkout. Checkout references are bound to the authenticated
  organization, selected plan version, billing interval, and currency.
- **Upgrade:** effective immediately after verified provider completion.
  Apply provider-supported proration, show the exact charge first, and
  update entitlements only through the internal lifecycle processor.
- **Downgrade:** scheduled for the next billing-period boundary. Show
  affected members, environments, agents, keys, webhooks, retention, and
  usage before confirmation. Preserve data; block new activity after the
  downgrade if the organization remains above its new limits.
- **Cancellation:** self-service cancellation defaults to period end and
  clearly displays the final access date. Reactivation before that date
  removes the cancellation schedule. Immediate cancellation is an
  exceptional support/finance action with explicit confirmation, reason,
  and audit evidence.
- **After expiration:** return the organization to Free when eligible.
  Preserve data for the contracted retention window and allow export where
  policy permits; never silently delete resources to satisfy lower limits.
- **Refund baseline:** duplicate or erroneous charges are refunded.
  A first self-service purchase may be refunded within seven calendar days
  when usage is below 10% of the included allowance. Renewal and voluntary
  mid-cycle cancellations are otherwise non-refundable and receive access
  through period end. Statutory rights and Enterprise contracts override
  this draft policy. No automatic prorated refund is promised.
- **Promotions:** promotions are versioned, time-bound, non-stackable by
  default, and recorded separately from plan price. A promotion never
  changes entitlements unless it explicitly selects another plan version.
- **Excess resources:** never delete members, agents, environments,
  policies, reports, keys, or audit data automatically. Disable new
  creation/reactivation until counts are within the effective plan.

### 10.6 Approval gates

The hosted subscription launch is blocked until named owners approve:

1. Organization-as-payer and whether account-required access applies only
   to hosted services (recommended) or also to offline SDK execution.
2. Plan names, prices, currency, annual discount, trial terms, included
   volume, overage rates, and support targets.
3. OAuth/OIDC identity provider, consumer login providers, MFA policy,
   Enterprise federation scope, and account-recovery policy.
4. Billing provider versus merchant-of-record model, tax handling,
   invoice numbering, currencies, and regions.
5. Upgrade proration, failed-payment cadence, downgrade enforcement,
   cancellation, refund, dispute, pause, and promotion policies.
6. Customer-visible history, archive/export, deletion, legal hold, and
   post-cancellation retention rules.
7. Business/Enterprise SLA, support escalation, temporary entitlement,
   refund approval, and two-person approval thresholds.
8. Cost model and pilot evidence validating the draft prices and limits.
