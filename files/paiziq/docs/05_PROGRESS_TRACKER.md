# Paiziq Progress Tracker

Human-readable implementation status against
`03_BUILD_DEPLOY_PLAN.md`. Update this file in the same PR as the
change it describes (see `04_DEVELOPER_GUIDE.md`, section 3).

**Last updated:** 2026-07-26 · **Current version:** 0.2.0

Legend: ✅ done · 🔄 in progress · ⬜ not started

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
| Project rules for collaborators/agents | ✅ | `.cursor/rules/`, `AGENTS.md` |
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
