# Paiziq Agent Audit Tracer — Build & Deployment Plan

**Scope:** Backend SDK and its path to production. Dashboard frontend is a parallel workstream consuming the contracts defined here.

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

- Dashboard review round-trip: `needs_review` decisions surface in the review queue; approvals delivered to the SDK via signed webhook → `approve_review` (or short-poll fallback for serverless agents).
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
