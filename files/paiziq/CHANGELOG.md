# Changelog

All notable changes to the Paiziq SDK and services are documented here,
per change, with versions. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Developer documentation site (`docs/site/`) — static React/Babel
  single-page app implementing the "Payment Agent SDK Guide" design
  handoff (deep charcoal + amber theme). Eight pages: overview,
  quickstart, authentication, how auditing works, notifications,
  recipes, API reference, changelog. Animated audit-pipeline graph,
  ⌘K command palette, light/dark themes. Serve with
  `python3 -m http.server` from `docs/site/`.
- Project rules for collaborators and agents: `.cursor/rules/`
  (change workflow, SDK core invariants, docs rules) and the
  harness-agnostic summary `AGENTS.md`.

## [0.2.0] - 2026-06-09

Phase 1 hardening: production stores, PII scrubbing, ingest service,
automation, and developer documentation.

### Added
- `RedisBudgetStore` (`paiziq.engine.stores`) — shared, atomic spend
  ledger over Redis sorted sets for multi-process agent fleets.
  Accepts an injected client or a URL (lazy `redis` import, installed
  via `pip3 install 'paiziq[redis]'`).
- `PostgresAuditStore` (`paiziq.audit.postgres`) — durable append-only
  audit trail over any DB-API 2.0 connection. Lazy `psycopg` import,
  installed via `pip3 install 'paiziq[postgres]'`.
- `PIIScrubber` and `ScrubbingExporter` (`paiziq.tracing.scrub`) —
  redact emails, card numbers, SSNs, and configured keys from spans
  before export. Wraps any `Exporter`; scrubbing failures never break
  the agent.
- FastAPI ingest service (`services/ingest`) — `POST /v1/traces`
  (idempotent span upsert), `POST /v1/notifications`, read-back GETs,
  Bearer API-key auth, request-size and batch-size limits, SQLite
  storage behind a swappable `IngestStore`.
- Property-based tests (Hypothesis) for decision rules and concurrency
  tests for `BudgetTracker` / `HTTPExporter`.
- `Makefile` with automated commands (`make help` lists all targets).
- GitHub Actions CI (`.github/workflows/ci.yml`): lint, tests with
  coverage on Python 3.10/3.12, examples, ingest tests, dist build.
- Developer guide (`docs/04_DEVELOPER_GUIDE.md`) and progress tracker
  (`docs/05_PROGRESS_TRACKER.md`).

### Changed
- `paiziq.__version__` bumped to `0.2.0`; new classes re-exported from
  the top-level `paiziq` package.
- `pyproject.toml`: added `redis` and `postgres` optional extras;
  added `hypothesis` to the dev extra.

## [0.1.0] - 2026-06-09

Phase 0 foundation (initial release).

### Added
- Zero-dependency core SDK (`paiziq`): models, decision engine with
  explainable rules, 4-way audit, policy and budget tracking.
- Tracing: `Tracer`, `Span`, `ConsoleExporter`, `InMemoryExporter`,
  `HTTPExporter` (stdlib-only).
- In-memory and JSONL audit stores; `MockGateway` payment gateway.
- Framework integrations: LangChain-style and OpenAI tool wrappers.
- 44-test baseline suite and runnable examples
  (`examples/happy_path.py`, `examples/framework_integrations.py`).
- Scope documents: executive scope, architecture, build & deploy plan.
