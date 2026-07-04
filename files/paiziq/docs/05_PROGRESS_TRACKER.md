# Paiziq Progress Tracker

Human-readable implementation status against
`03_BUILD_DEPLOY_PLAN.md`. Update this file in the same PR as the
change it describes (see `04_DEVELOPER_GUIDE.md`, section 3).

**Last updated:** 2026-06-10 · **Current version:** 0.2.0

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
(local/CI; CodeArtifact pending) · traces visible in dashboard ⬜
(dashboard is a parallel workstream).

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
| PZ-016 | Payment proposal persistence + state transitions | ✅ | `routers/payments.py`, `stores/payments.py` |
| PZ-017 | Decision engine service boundary | ✅ | `routers/decisions.py`, `stores/decisions.py` |
| PZ-028 | SDK domain model validation | ✅ | `models.py`, `engine/policy.py` `__post_init__` validation |
| PZ-032 | SDK async HTTP transport (retries/backoff) | ✅ | `transport.py` (`AsyncHTTPTransport`, `RetryPolicy`) |
| PZ-033 | SDK sync HTTP transport (retries) | ✅ | `transport.py` (`SyncHTTPTransport`); optional in `HTTPExporter` |
| PZ-035 | SDK safe failure modes | ✅ | `FailureMode` in `models.py`; mapped in `sdk.py` |
| PZ-036 | SDK structured logging + debug mode | ✅ | `logging.py` (`log_event`, `debug()`, redaction) |
| PZ-038 | SDK webhook signature verification | ✅ | `webhooks.py` (HMAC-SHA256, replay window) |
| PZ-044 | Test payment agent uses SDK end-to-end | ✅ | `examples/payment_agent.py`; in `make examples` |
| PZ-022 | Policy versioning + immutable published snapshots | ✅ | `routers/policies.py`, `stores/policies.py`, `policy_doc.py` |
| PZ-023 | Policy draft/publish/rollback/compare APIs | ✅ | `routers/policies.py` (rollback, versions/compare) |
| PZ-024 | Policy simulator API | ✅ | `routers/policies.py` (`POST /v1/policies/simulate`) |

## Phase 2 — Pilot Readiness

| Item | Status |
| --- | --- |
| Dashboard review round-trip (webhook → `approve_review`) | ⬜ |
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
