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
