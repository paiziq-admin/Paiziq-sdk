# Paiziq Progress Tracker

Human-readable implementation status against
`03_BUILD_DEPLOY_PLAN.md`. Update this file in the same PR as the
change it describes (see `04_DEVELOPER_GUIDE.md`, section 3).

**Last updated:** 2026-10-04 · **Current version:** 0.3.0

Legend: ✅ done · 🔄 in progress · ⬜ not started

## Site parity — Phase 0 execution and evidence foundation ✅

Completed locally on 2026-10-04 in SDK 0.3.0, the ingest service, and the
companion dashboard. Three implementation agents worked in parallel on the
SDK, backend and frontend. The root agent integrated the hosted adapter,
reviewed cross-component behavior, and ran the final backend gate. This
workstream is distinct from the historical production scaffold below.

| ID | Area | To-do | Status | Owner / evidence |
| --- | --- | --- | --- | --- |
| SP0-01 | Design | Agree ledger, gateway, HTTP and UI contracts | ✅ | Root + three agents; shared SQLite algorithm and explicit authority |
| SP0-02 | SDK/backend | Atomic scoped, currency-aware reservations and execution claims | ✅ | SDK; concurrent duplicate, warning-band and two-$60/$100 regressions |
| SP0-03 | SDK/backend | Immutable request, policy and review evidence; revalidate before execution | ✅ | SDK + backend; full-payload change, stale approval, policy and mandate expiry tests |
| SP0-04 | SDK | Idempotent provider seam; unknown outcomes and receipt reconciliation | ✅ | SDK; timeout, restart, lost response and accounting failure tests |
| SP0-05 | Backend | Hosted decisions use shared committed and reserved history | ✅ | Backend; exact budget, velocity, currency and historical-spend tests |
| SP0-06 | Backend | Durable events and outbox; idempotent publication | ✅ | SDK + backend; transaction, restart, immutable SQL evidence and publication replay tests |
| SP0-07 | Backend | Scoped idempotency, authorization and managed-execution transition guards | ✅ | Backend; scope, conflict, token, legacy bypass and webhook evidence access tests |
| SP0-08 | Full stack | SDK hosted execution adapter uses one authoritative ledger | ✅ | Root; 10 real HTTP SDK tests with mock providers |
| SP0-09 | Frontend | Execution status, reserved exposure and immutable evidence panel | ✅ | Frontend; API/component tests and live-service workflow |
| SP0-10 | Frontend | Remove unsupported manual execution claims; show unknown and unavailable states | ✅ | Frontend; legacy unverified state, 404/403/429 and no retry control |
| SP0-11 | Full stack | OpenAPI, generated types, migrations and compatibility | ✅ | Backend; migrations 0009–0011, schema-8 upgrade and generated-contract checks |
| SP0-12 | Verification | Backend quality gate and full-stack service/browser workflow | ✅ | Root + agents; 228 SDK + 170 ingest tests, 3 examples, 2 live-service browser workflows |
| SP0-13 | Verification | Frontend gate, themes, responsive layouts and error states | ✅ | Frontend; 24 tests, 8 fixture browser workflows, light/dark and 320/390/900/1440px checks |
| SP0-14 | Documentation | Plan, tracker, changelog, guides and final plain-English explanation | ✅ | Root + frontend; guides, contracts, docs site, generated dashboard docs and browser proof |

Acceptance evidence: repeated or concurrent logical execution causes one
mock provider invocation through the managed path. Two preapproved $60
requests cannot spend $120 against a $100 budget. Unknown results keep their
reservation across a restart and beyond budget-window expiry. The live
browser run showed $40 committed, $20 reserved, and a blocked new $50 request.

Proof: [dashboard run report](../../../../Paiziq-Dashboard/docs/phase0-evidence/phase0-workflow.json),
[execution screenshot](../../../../Paiziq-Dashboard/docs/phase0-evidence/phase0-unknown-execution.png),
and [frontend tracker](../../../../Paiziq-Dashboard/docs/implementation-status.md).

### Completion explanation — simplified technical English

The SDK reserves money before it sends a payment. A repeated request uses the
existing execution record. It does not send another payment. The backend
checks the current policy, budget and authorization before submission.

An unknown provider result keeps the reservation. The operator must check
the provider record before the service releases that reservation. A failure
to save a confirmed result does not change the payment to a failed payment.

The dashboard shows the execution state, reserved amount and recorded
evidence. It identifies old external reports as unverified. It does not
provide a button that declares payment success.

Use a shared SQLite file or the hosted adapter for persistent execution
control. The default local database is in memory. The execution guarantee
applies to `execute_payment()` and the hosted claim/report protocol.
Review-only framework wrappers must use this path for the actual charge.
Provider evidence comes from a trusted executor or operator. A provider
without idempotency and receipt lookup cannot provide a general exactly-once
guarantee. This phase used mock providers. No production deployment occurred.

Scores, economic checks, compute spend and outcome checks remain in later
phases. Phase 0 supplies the execution and evidence foundation for that work.

## Phase 0 — Production Scaffold (v0.1.0) ✅

| Item | Status | Where |
| --- | --- | --- |
| `/sdk` package, src layout, importable locally | ✅ | `sdk/` |
| Decision engine (threshold, lists, budget, intent, review) | ✅ | `sdk/src/paiziq/engine/` |
| Explainable verdicts with reasons + risk flags | ✅ | `engine/rules.py` |
| 4-Way Match audit | ✅ | `engine/audit4.py` |
| `PaiziqSDK` facade (review/execute/audit trail/approve) | ✅ | `sdk.py` |
| Tracer + Console/InMemory/HTTP exporters | ✅ | `tracing/tracer.py` |
| Notifications with webhook delivery | ✅ | `notify.py` |
| Generic / LangChain / OpenAI integrations | ✅ | `tracing/integrations.py` |
| 44-test baseline + runnable examples | ✅ | `sdk/tests/`, `sdk/examples/` |

## Phase 1 — Production Hardening (v0.2.0)

### Week 1 · Quality gates & packaging

| Item | Status | Where / Notes |
| --- | --- | --- |
| GitHub Actions CI (ruff, pytest --cov, 3.10/3.12 matrix) | ✅ | `.github/workflows/ci.yml` |
| Build artifacts (`python3 -m build`) in CI | ✅ | CI `build` job, `make build` |
| CHANGELOG + semver policy | ✅ | `CHANGELOG.md`, guide §5 |
| Publish to AWS CodeArtifact on tags | ⬜ | needs AWS account wiring |
| Property-based tests (Hypothesis) on rule boundaries | ✅ | `tests/test_hardening_extra.py` |
| Concurrency tests (BudgetTracker, HTTPExporter) | ✅ | `tests/test_hardening_extra.py` |

### Week 2 · Production stores

| Item | Status | Where / Notes |
| --- | --- | --- |
| `RedisBudgetStore` (sorted-set spend windows) | ✅ | `engine/stores.py`, extra `paiziq[redis]` |
| `PostgresAuditStore` (append-only, indexed) | ✅ | `audit/postgres.py`, extra `paiziq[postgres]` |
| PII-scrubbing hook on span export | ✅ | `tracing/scrub.py` (`ScrubbingExporter`) |

### Week 3 · Ingest plane (minimum viable cloud)

| Item | Status | Where / Notes |
| --- | --- | --- |
| FastAPI ingest: traces + notifications, auth, limits, idempotent upsert | ✅ | `services/ingest/` (9 tests) |
| Terraform (ECS, ALB, SQS, RDS, ElastiCache, Secrets, alarms) | ⬜ | requires cloud account |
| Load test (1k spans/s, drop-rate < 0.1%) | ⬜ | after Terraform deploy |

### Developer experience (unreleased)

| Item | Status | Where / Notes |
| --- | --- | --- |
| Payment-agent full-stack E2E suite and tutorial | ✅ | `docs/e2e/`, `tests/e2e_support/`, Make E2E commands, dashboard fixture/service lanes |
| Backend deployment package (container, Azure Container Apps script, smoke test, Northstar demo runner) and deployment guide | ✅ package · 🔄 Azure subscription suspension | `services/ingest/Dockerfile`, `entrypoint.sh`, `deploy/azure/`, `scripts/smoke_backend.py`, `scripts/northstar_demo.py`, `docs/deploy/AZURE_DEPLOYMENT_GUIDE.md`; backend and hosted demo verified 2026-10-03; on 2026-10-04 subscription state `Warned`, Container Apps reports `ManagedClusterSuspended`; Owner action required; dashboard live frontend published |
| LangChain procurement agent (catalog purchases through PaiziqSDK, published policy, dashboard trace) | ✅ | `sdk/examples/procurement_agent.py`, `make procurement-demo`; live hosted-LLM run still needs an operator-supplied free-tier key |
| Project rules for collaborators/agents | ✅ | `.cursor/rules/`, `AGENTS.md` |
| Main-branch Azure CI and contributor notifications | ✅ CI/notifications · 🔄 backend role grants | Root `.github/workflows/ci.yml`, `notify-ci.yml`; deployed hostname/CORS discovery supports Terraform recreation; hosted SDK quality/build/container smoke passed, failure notification delivered; dashboard quality/E2E/deploy and success notification passed; Azure Owner must grant app Contributor and registry AcrPush to the deployment identity |
| Developer documentation site (design handoff) | ✅ | `docs/site/` — serve with `python3 -m http.server` |

**Phase 1 exit criteria:** CI green ✅ · versioned installable package ✅
(local/CI; CodeArtifact pending) · traces render in the companion live
dashboard ✅ (staging/cloud deployment remains tied to the pending
infrastructure work).

## Backend API build-out (PZ backlog)

Ordered backlog for the backend/SDK build-out. IDs come from the
product task list; statuses update as each item starts/completes.

| ID | Item | Status | Where / Notes |
| --- | --- | --- | --- |
| PZ-007 | API contract (ingestion, decisions, reviews, policies, agents, audit logs) | ✅ | `docs/06_API_CONTRACT.md` |
| PZ-008 | OpenAPI specification + generated client types | ✅ | `services/ingest/openapi.json`, `paiziq.api_types`, `make openapi` |
| PZ-009 | Backend service scaffold, production configuration | ✅ | `services/ingest/config.py`, `.env.example`, `Dockerfile` |
| PZ-010 | Schema migrations (tenants, agents, events, payments, reviews, policies, audit logs) | ✅ | `services/ingest/migrations.py`, `migrations/000*.sql` |
| PZ-011 | Organization & environment management APIs | ✅ | `routers/orgs.py`, `stores/orgs.py` |
| PZ-012 | Agent registration & metadata APIs | ✅ | `routers/agents.py`, `stores/agents.py` |
| PZ-013 | API key create/display-once/rotate/revoke | ✅ | `routers/keys.py`, `stores/keys.py`, migration `0003` |
| PZ-016 | Payment proposal persistence + state transitions | ✅ | Server currency/amount/text/time filters, deterministic sort, exact pagination, and open-review transition guard in `routers/payments.py`, `stores/payments.py` |
| PZ-017 | Decision engine service boundary | ✅ | Immutable re-evaluation, open-review reuse, and terminal-verdict guard in `routers/decisions.py`, `stores/decisions.py` |
| PZ-101 | Human-review queue, identity, assignment, actions, notes, priority, and SLA API | ✅ | Key-name/tenant/role binding, atomic resolution, and migration/backfill/index evidence in `routers/reviews.py`, `stores/decisions.py`, `migrations/0008_review_workflows.sql`, `tests/test_reviews.py`, `tests/test_migrations.py` |
| PZ-028 | SDK domain model validation | ✅ | `models.py`, `engine/policy.py` `__post_init__` validation |
| PZ-032 | SDK async HTTP transport (retries/backoff) | ✅ | `transport.py` (`AsyncHTTPTransport`, `RetryPolicy`) |
| PZ-033 | SDK sync HTTP transport (retries) | ✅ | `transport.py` (`SyncHTTPTransport`); optional in `HTTPExporter` |
| PZ-035 | SDK safe failure modes | ✅ | `FailureMode` in `models.py`; mapped in `sdk.py` |
| PZ-036 | SDK structured logging + debug mode | ✅ | `logging.py` (`log_event`, `debug()`, redaction) |
| PZ-038 | SDK webhook signature verification | ✅ | `webhooks.py` (HMAC-SHA256, replay window) |
| PZ-044 | Test payment agent uses SDK end-to-end | ✅ | `examples/payment_agent.py`; in `make examples` |
| PZ-022 | Policy versioning + immutable published snapshots | ✅ | `routers/policies.py`, `stores/policies.py`, `policy_doc.py` |
| PZ-023 | Policy draft/publish/rollback/compare APIs | ✅ | Draft audit reason plus rollback and versions/compare in `routers/policies.py` |
| PZ-024 | Policy simulator API | ✅ | `routers/policies.py` (`POST /v1/policies/simulate`) |
| PZ-043 | SDK integration tests against local backend | ✅ | `services/ingest/tests/test_sdk_integration.py` (uvicorn + real SDK) |
| PZ-045 | Test agent scenarios: approvals/reviews/rejects/duplicates/gateway errors | ✅ | `examples/payment_agent.py` scenarios 1–8 |
| PZ-041 | SDK package publish/release workflow | ✅ | `.github/workflows/release.yml`, `tests/test_version.py` |
| PZ-039 | SDK dashboard deployment command | ✅ | `paiziq dashboard deploy/serve` (`cli/dashboard.py`) |
| PZ-073 | RBAC roles on API keys | ✅ | `auth.py`, migration `0007`, `stores/keys.py` |
| PZ-074 | Audit log read API + ingest coverage | ✅ | `audit.py`, `routers/audit.py` |
| PZ-076 | Webhook delivery engine | ✅ | Exact environment/event/payment/review lookup, retry/DLQ, and attempts in `stores/webhooks.py`, `webhook_worker.py` |
| PZ-077 | Signed outbound webhooks | ✅ | `webhook_sign.py`, `field_secrets.py` |
| PZ-078 | Notification routing / review SLA | ✅ | `event_router.py` |
| PZ-079 | Metrics aggregation | ✅ | Grouped decision/payment/risk-flag summary and `payments.total` timeseries in `stores/metrics.py`, `routers/metrics.py` |
| PZ-080 | Event search indexing | ✅ | migration `0006`, `routers/search.py` |
| PZ-081 | Data retention controls | ✅ | `retention.py`, `routers/admin.py` |
| PZ-082 | Encryption at rest for webhook secrets | ✅ | `field_secrets.py`, `cryptography` |
| PZ-083 | Rate limiting | ✅ | `rate_limit.py`, middleware in `app.py` |
| PZ-084 | CORS configuration | ✅ | `config.py`, `app.py` |
| PZ-040 | SDK CLI (init/login/agents/keys/dashboard/replay) | ✅ | `sdk/src/paiziq/cli/`, console script `paiziq` |

## Hosted account and subscription commercialization backlog

This backlog implements the draft source of truth in product-scope section
10, architecture section 10, and the commercialization program in the
build/deploy plan. A checked planning row does not imply that its runtime
capability is shipped.

| ID | Item | Status | Where / Exit evidence |
| --- | --- | --- | --- |
| PZ-102 | Commercial customer model, feature catalog, plan matrix, lifecycle, environment, and launch plan | ✅ | `docs/01_PRODUCT_SCOPE.md` §10, `docs/02_ARCHITECTURE.md` §10, `docs/03_BUILD_DEPLOY_PLAN.md` commercialization program |
| PZ-103 | Deny-by-default organization/environment isolation on every resource | ⬜ | Ownership backfill plus exhaustive cross-tenant list/detail/mutation tests |
| PZ-104 | Reproducible local/development/production deployment profiles | ⬜ | Bootable production image, managed persistence/queue plan, backups/restore, environment smoke tests |
| PZ-105 | OAuth/OIDC login and secure server-managed user sessions | ⬜ | PKCE/state/nonce/issuer, CSRF, expiry/revocation, no production bypass |
| PZ-106 | Users, organization memberships, invitations, and human RBAC | ⬜ | Account-first onboarding and role/tenant enforcement |
| PZ-107 | Versioned feature, plan, price, and plan-entitlement catalog | ⬜ | Approved immutable plan versions seeded per deployment |
| PZ-108 | Central server-side entitlement decision service | ⬜ | Permission/status/feature/limit outcomes plus cache revision tests |
| PZ-109 | Idempotent usage events, atomic counters, resets, limits, and notices | ⬜ | Unique-payment meter, no rollover, rebuild/reconciliation evidence |
| PZ-110 | Internal subscription lifecycle and append-only transitions | ⬜ | Free/trial/active/dunning/suspended/canceled/expired state tests |
| PZ-111 | Billing-provider adapter and idempotent hosted checkout | ⬜ | Account-first monthly/annual sandbox checkout without raw card handling |
| PZ-112 | Verified inbound provider-event ledger, worker, outbox, retry/DLQ, and replay | ⬜ | Duplicate, delayed, reordered, crash/restart, and partial-failure evidence |
| PZ-113 | Renewal, dunning, upgrades, downgrades, cancellation, refunds, disputes, and reactivation | ⬜ | Deterministic lifecycle jobs and customer notifications |
| PZ-114 | Dashboard account, pricing, checkout, usage, and self-service billing area | ⬜ | Exact dates/charges, invoice access, plan changes, cancellation/reactivation |
| PZ-115 | Support, billing-operations, finance, product-operations, and admin workflows | ⬜ | Reason/before-after audit and two-person approval controls |
| PZ-116 | Daily financial reconciliation, commercial metrics, monitoring, and runbooks | ⬜ | Owned exception report and alert evidence |
| PZ-117 | OAuth/tenancy suite plus all 20 subscription business scenarios | ⬜ | Customer/internal/access/invoice/notification/audit/reporting verification |
| PZ-118 | Internal, limited-beta, controlled, and GA launch gates | ⬜ | Signed acceptance criteria and live low-value purchase/refund evidence |

## Phase 2 — Pilot Readiness

| Item | Status |
| --- | --- |
| SDK callback bridge from a control-plane review webhook to in-process `approve_review` (separate from the shipped PZ-101 dashboard queue) | ⬜ |
| `StripeGateway` (sandbox) + gateway conformance kit | ⬜ |
| Server-side LLM Intent Judge | ⬜ |
| Design-partner pilot instrumentation | ⬜ |

## Phase 3 — GA Track

| Item | Status |
| --- | --- |
| SD-JWT mandate signing/verification | ⬜ |
| Mastercard Agent Pay connector; multi-gateway routing | ⬜ |
| OTLP exporter bridge; TypeScript SDK kickoff | ⬜ |
| SOC 2 evidence pack | ⬜ |

## Verification log

| Date | Version | Gate | Result |
| --- | --- | --- | --- |
| 2026-06-09 | 0.1.0 | baseline pytest | 44 passed |
| 2026-06-09 | 0.2.0 | `make test` (SDK) | 66 passed |
| 2026-06-09 | 0.2.0 | `make ingest-test` | 9 passed |
| 2026-06-09 | 0.2.0 | `make check` (lint + tests + examples) | all passed |
| 2026-06-09 | 0.2.0 | coverage | 89% total; engine/ package 97% (gate ≥ 90%) |
| 2026-06-10 | unreleased | docs site render check (browser, all 8 pages load; API/recipes verified) | passed |
| 2026-07-03 | unreleased | `make check` after PZ-007/PZ-008 (69 SDK + 13 ingest tests, examples) | all passed |
| 2026-07-03 | unreleased | mypy on SDK incl. generated `api_types` (17 files) | no issues |
| 2026-07-03 | unreleased | `make check` after PZ-009 (69 SDK + 22 ingest tests, examples) | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-010 (69 SDK + 30 ingest tests, examples) | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-011 (69 SDK + 40 ingest tests, examples); mypy clean | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-012 (69 SDK + 49 ingest tests, examples); mypy clean | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-013 (69 SDK + 61 ingest tests, examples); mypy clean | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-016 (69 SDK + 71 ingest tests, examples); mypy clean | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-017 (69 SDK + 80 ingest tests, examples); mypy clean | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-028 (109 SDK + 80 ingest tests, examples); mypy clean | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-032 (121 SDK + 80 ingest tests, examples); mypy clean (18 files) | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-033 (127 SDK + 80 ingest tests, examples); mypy clean | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-035 (137 SDK + 80 ingest tests, examples); mypy clean | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-036 (145 SDK + 80 ingest tests, examples); mypy clean (19 files) | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-038 (165 SDK + 80 ingest tests, examples); mypy clean (20 files) | all passed |
| 2026-07-03 | unreleased | `make check` after PZ-044 (165 SDK + 80 ingest tests, 3 examples incl. payment_agent); mypy clean | all passed |
| 2026-07-03 | unreleased | Final wrap-up gate for PZ-013–PZ-044 batch: `make check` + mypy after README/architecture/dev-guide sync | all passed |
| 2026-07-04 | unreleased | `make check` after PZ-022 (165 SDK + 88 ingest tests, examples) | all passed |
| 2026-07-04 | unreleased | `make check` after PZ-023 (165 SDK + 93 ingest tests, examples) | all passed |
| 2026-07-04 | unreleased | `make check` after PZ-024 (165 SDK + 98 ingest tests, examples) | all passed |
| 2026-07-04 | unreleased | `make check` after PZ-043 (165 SDK + 102 ingest tests incl. SDK-over-HTTP integration) | all passed |
| 2026-07-04 | unreleased | `make check` after PZ-045 (payment agent scenarios 1–8 incl. duplicate + gateway error) | all passed |
| 2026-07-04 | unreleased | `make check` after PZ-041 (167 SDK tests incl. version consistency) | all passed |
| 2026-07-04 | unreleased | `make check` after PZ-073–084 webhook/metrics/security batch | all passed |
| 2026-07-26 | unreleased | Final `make check` after PZ-101 and dashboard-query hardening (181 SDK + 124 ingest tests, generated contract, 3 examples); mypy clean (25 files) | all passed |
| 2026-07-26 | unreleased | Final backend integrity audit: 116 non-network ingest tests; 47 focused review/payment/OpenAPI tests; 4 OpenAPI-sync tests; targeted Ruff; `git diff --check` | all passed |
| 2026-07-26 | unreleased | `make check` after PZ-102 account/subscription planning docs (Ruff, 181 SDK tests, 124 ingest tests, 3 examples) | all passed |
| 2026-10-03 | unreleased | Payment-agent E2E: `make check` (181 SDK + 127 ingest tests, 3 examples), focused I1–I3, 40-response endpoint verification, targeted Ruff, diff check; dashboard 6 fixture + 1 live browser test and `npm run check` (11 unit tests); runnable tutorial | all passed |
| 2026-10-03 | unreleased | Deployment package: `make check` (181 SDK + 141 ingest tests incl. 14 deployment-lane tests, 3 examples); `make docker-smoke` 5/5 against the production-mode image; Northstar demo run against the container (approved / needs_review / rejected, read-only key, CORS allowed); `bash -n deploy_backend.sh`; targeted Ruff | all passed |
| 2026-10-03 | unreleased | Contributor-compatible deployment: `make check` (181 SDK + 145 ingest tests, including credential validation and secret-free create/update paths, 3 examples); targeted Ruff (E4/E7/E9/F); `make docker-smoke` with deployed dashboard CORS; shell syntax and diff checks | all passed; container smoke 5/5; local Ruff 0.4.10 |
| 2026-10-03 | unreleased | Azure rollout and deployment fixes: `make check` 181 SDK + 145 ingest tests; single-writer update order, fresh revision suffix, existing RG preservation; ARM64 and Linux AMD64 container smoke 5/5; private image pushed; East US 2 storage ready | local gates passed; hosted API pending Azure cleanup/global environment quota; cloud build unavailable, local push used |
| 2026-10-03 | unreleased | Completed Azure dev backend rollout: `paiziq-ingest-dev`, East US 2; hosted smoke, managed-key trace ingestion/read, org/env/agent setup, revision restart, Swagger/OpenAPI | smoke 5/5; original trace, agent and SDK/read keys persist after restart; one healthy active revision/replica; no gateway execution |
| 2026-10-03 | unreleased | `make northstar-demo` against Azure; independent API readback with issued demo read-only key | approved/executed (MockGateway), needs_review, rejected; 3 payment/decision/SDK trace sets verified, policy v1, 1 open review, dashboard CORS allowed; report secret-free and read key saved to gitignored `.e2e/northstar.env` |
| 2026-10-03 | unreleased | Final Northstar documentation gate: `make check` (181 SDK + 145 ingest tests, 3 examples); diff and credential exclusion checks | all passed; demo report and credentials remain gitignored |
| 2026-10-03 | unreleased | Main CI setup: `make check build` (181 SDK + 145 ingest tests, 3 examples, sdist/wheel); Linux AMD64 `make docker-smoke`; hosted credential-free `make ci-smoke`; actionlint for both repositories; dashboard quality and browser gates | all local gates passed; container smoke 5/5, hosted CI smoke 4/4, dashboard 11 unit + 6 Chromium tests; OIDC identity created, Owner role assignments pending |
| 2026-10-03 | unreleased | GitHub-hosted CI: SDK run `37175728093`, dashboard push run `37175628199`; contributor notifications; both repositories' development/main ref equality | SDK quality/build/container smoke passed; Azure OIDC login blocked by missing role assignments (`No subscriptions found`); dashboard deployed successfully; SDK failure and dashboard success notifications posted to issues #2/#3; earlier SDK PR #1 remains open/unmerged |
| 2026-10-04 | unreleased | LangChain procurement agent: `make check` (181 SDK + 143 ingest tests incl. 2 procurement-agent tests, 3 examples); targeted Ruff on the example and test | all passed |
| 2026-10-04 | unreleased | Procurement alerts: `make check` (181 SDK + 144 ingest tests, 3 examples); review and rejection notifications posted to the deployed backend for the live Groq run | all passed |
| 2026-10-04 | 0.3.0 | Phase 0 first full `make check`: SDK/service tests passed; happy-path example expected mutation of an immutable review | failed example; fixed by reading persisted execution evidence |
| 2026-10-04 | 0.3.0 | Final Phase 0 `make check`: Ruff, 228 SDK tests, 170 ingest tests, generated contracts, 3 runnable examples | all passed; 2 dependency deprecation warnings |
| 2026-10-04 | 0.3.0 | `make typecheck build`: 27 source files; sdist and wheel | all passed |
| 2026-10-04 | 0.3.0 | Dashboard `npm run check` (24 tests, lint, types, build, docs), 8 fixture browser workflows, 2 real-service browser workflows | all passed; responsive light/dark evidence saved |
| 2026-10-04 | 0.3.0 | Docs site: all 15 JSX files parsed; API/quickstart/concepts/recipes/changelog browser review | passed; no JS runtime errors; existing favicon 404/Babel development warning |
| 2026-10-04 | 0.3.0 | Built wheel smoke with site packages disabled (`python3 -S`): stdlib imports, managed execution and repeated-ID replay; both repository diff checks | passed; one mock provider call |
| 2026-10-04 | 0.3.0 | PR #1 conflict resolution and combined deployment template: `make check typecheck build` (228 SDK + 179 ingest tests, 3 examples, 27 typed source files, wheel/sdist); Linux AMD64 container smoke; Makefile target regression, actionlint, shell syntax and diff checks | all local gates passed; container smoke 5/5; hosted verification blocked by subscription `Warned` / `ManagedClusterSuspended` and missing CI identity roles |
| 2026-10-04 | unreleased | Terraform recreation integration: `make check` (228 SDK + 179 ingest tests, 3 examples); CI deployment tests verify live backend/CORS discovery overrides stale endpoints; shell syntax, actionlint and diff checks | all local gates passed; live Azure still blocked by subscription suspension and missing CI role grants |
