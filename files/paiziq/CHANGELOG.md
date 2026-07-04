# Changelog

All notable changes to the Paiziq SDK and services are documented here,
per change, with versions. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Tag-driven SDK release workflow (PZ-041):
  `.github/workflows/release.yml` verifies the `v*` tag matches
  `pyproject.toml` and `paiziq.__version__`, runs the suite, builds
  sdist+wheel, creates a GitHub release with artifacts, and publishes
  to PyPI when `PYPI_API_TOKEN` is configured (skips cleanly
  otherwise). Version consistency enforced by `tests/test_version.py`.

- Test payment agent duplicate and gateway-error scenarios (PZ-045):
  resubmitting an executed proposal trips the `max_tx_per_hour`
  velocity guard (needs_review + `velocity_anomaly` flag), and a flaky
  gateway shows a failed charge recording an error without committing
  budget spend, then succeeding on retry.
- SDK-to-backend integration tests (PZ-043):
  `services/ingest/tests/test_sdk_integration.py` boots uvicorn on an
  ephemeral localhost port and drives the real service with the real
  SDK — `HTTPExporter` trace export read back over the wire, webhook
  notifications on needs_review, a full control-plane round trip
  (org → env → agent → payment → decision) through
  `SyncHTTPTransport`, and auth-failure surfacing. Offline-safe.
- Policy simulator (PZ-024): `POST /v1/policies/simulate` evaluates a
  hypothetical payment with the deterministic SDK engine against an
  inline document, a policy draft, a specific/latest published version,
  or an environment's active policy — nothing persists, no payment
  state changes, read scope suffices.
- Policy rollback and version compare (PZ-023):
  `POST /v1/policies/{id}/rollback` publishes a new version copying an
  older document (history never rewritten, draft re-synced) and
  `GET /v1/policies/{id}/versions/compare?base=&target=` diffs two
  refs (version number or `draft`) returning only changed fields.
- Policy versioning with immutable published snapshots (PZ-022):
  `POST/GET /v1/policies`, `PUT /v1/policies/{id}/draft`,
  `POST /v1/policies/{id}/publish`, and version history endpoints
  (`services/ingest/routers/policies.py`, `stores/policies.py`,
  `policy_doc.py`). Policy documents are the JSON form of the SDK
  `PaymentPolicy` and validated by it; published versions are
  append-only with exactly one active version per environment.
  `POST /v1/decisions` now evaluates with the environment's active
  policy and records its `policy_version` (previously always null).
- Test payment agent example (PZ-044): `sdk/examples/payment_agent.py`
  drives approved / needs-review-with-human-approval / rejected /
  engine-outage / webhook-verification flows entirely through
  `PaiziqSDK` (no policy logic in the agent), exporting spans through
  the retrying `SyncHTTPTransport` against an in-memory endpoint.
  Wired into `make examples` (and thus `make check`).
- SDK webhook signature verification (PZ-038): `paiziq.webhooks` with
  `verify_webhook_signature(payload, signature, secret, tolerance_s)`
  (HMAC-SHA256 over `t=...,v1=...` headers, constant-time compare,
  two-sided replay-window check, malformed input returns False instead
  of raising) and `sign_webhook_payload` for producing signatures.
  Both re-exported from `paiziq`.
- SDK structured logging + debug mode (PZ-036): stdlib-only
  `paiziq.logging` helper with `get_logger` (namespaced under
  `paiziq.*`), `log_event` (structured `key=value` records with
  unconditional secret redaction — api keys/tokens/secrets never reach
  a log line), and a `paiziq.debug()` toggle that enables verbose
  decision/transport logs. All re-exported from `paiziq`.
- SDK safe failure modes (PZ-035): `FailureMode` enum (`fail_open` /
  `fail_closed` / `review_required`) on the `PaiziqSDK` facade. When
  the decision engine raises unexpectedly, the SDK maps the failure to
  a deterministic approved/rejected/needs_review verdict (default:
  fail closed) with a machine-readable `failure_mode:*` reason and an
  audit-trail entry, instead of raising into the agent.
- SDK sync HTTP transport (PZ-033): `SyncHTTPTransport` shares the
  same `RetryPolicy` object as the async transport (identical
  retry/backoff semantics, injectable `sleep` for tests).
  `HTTPExporter` accepts an optional `transport=` to route span export
  through it — default behavior and the frozen wire contract are
  unchanged, and export failures still never raise into the agent.
- SDK async HTTP transport (PZ-032): stdlib-only `paiziq.transport`
  module with `AsyncHTTPTransport` (blocking `urllib` I/O in an
  executor, `asyncio.sleep` backoff), a shared `RetryPolicy` (bounded
  exponential backoff with jitter; retries 429/5xx and connection
  errors, fails fast on other 4xx), `TransportResponse`, and
  `TransportError`. All re-exported from the top-level `paiziq`
  package.
- SDK domain-model validation (PZ-028): `PaymentRequest`, `Mandate`,
  and `PaymentPolicy` now reject malformed input at construction with
  clear `ValueError`s — positive finite amounts, 3-letter ISO 4217
  currency codes (normalized to upper case), non-empty
  agent/principal/merchant identifiers, and sane policy bounds
  (thresholds > 0, `hard_limit >= review_threshold`,
  `budget_warning_ratio` in (0, 1], positive budgets,
  `max_tx_per_hour >= 1`, valid `treat_unknown_merchant_as`).
- SDK domain-model validation (PZ-028): `PaymentRequest`, `Mandate`,
  and `PaymentPolicy` now reject malformed input at construction with
  clear `ValueError`s — positive finite amounts, 3-letter ISO 4217
  currency codes (normalized to upper case), non-empty
  agent/principal/merchant identifiers, and sane policy bounds
  (thresholds > 0, `hard_limit >= review_threshold`,
  `budget_warning_ratio` in (0, 1], positive budgets,
  `max_tx_per_hour >= 1`, valid `treat_unknown_merchant_as`).
- Decision engine service boundary (PZ-017): `POST /v1/decisions`
  evaluates a persisted payment with the deterministic SDK
  `DecisionEngine`, stores an immutable decision record (re-evaluation
  appends a new one), applies the matching payment state transition,
  and opens a review row when the verdict is `needs_review`. Plus
  `GET /v1/decisions[?payment_id]` and `GET /v1/decisions/{id}`
  (`stores/decisions.py`, `routers/decisions.py`). `policy_version` is
  null until policy management (PZ-022+) lands.
- Payment proposal persistence and state transitions (PZ-016):
  `POST /v1/payments` (tenancy-checked, `Idempotency-Key` replay
  returns the original payment), `GET /v1/payments` with
  `env_id`/`agent_id`/`state` filters, `GET /v1/payments/{id}` including
  the append-only `transitions` history, and
  `POST /v1/payments/{id}/transition` enforcing the server-side state
  machine (`proposed → approved/needs_review/rejected`,
  `approved → executed/failed`; violations → `409
  invalid_state_transition`). Audit-log entries on create and
  transition (`stores/payments.py`, `routers/payments.py`).
- API key lifecycle APIs (PZ-013): `POST /v1/api-keys` (server-generated
  `pzq_<env-kind>_…` secrets stored as SHA-256 hashes, plaintext shown
  exactly once), `GET /v1/api-keys`, `POST /v1/api-keys/{id}/rotate`
  (optional grace window during which the previous secret still
  validates), and `DELETE /v1/api-keys/{id}` (soft revoke). Bearer auth
  now resolves database-backed keys with `ingest`/`read`/`admin` scopes
  alongside the bootstrap env-var keys (`stores/keys.py`,
  `routers/keys.py`, migration `0003`, scope enforcement in `auth.py`).
- Agent registration and metadata APIs (PZ-012): `POST /v1/agents`
  (idempotent on env + name so agents can self-register at boot),
  `GET /v1/agents[?env_id]`, `GET /v1/agents/{id}`, and
  `PATCH /v1/agents/{id}` (name, active/disabled status, full-replace
  metadata) with audit-log entries (`stores/agents.py`,
  `routers/agents.py`).
- Organization and environment management APIs (PZ-011): `POST/GET
  /v1/orgs`, `GET /v1/orgs/{id}`, `POST/GET /v1/orgs/{id}/environments`
  with the contract envelope, pagination meta, 404/409/422 error codes,
  and audit-log entries on every mutation. New control-plane plumbing:
  `envelope.py` (envelope + `ApiError` handler), `auth.py` (shared
  Bearer auth + audit actor), `ids.py`, `audit.py` (append-only
  writer), `deps.py`, `stores/orgs.py`, `routers/orgs.py`.
- Versioned SQLite schema migrations for the ingest service (PZ-010):
  stdlib-only runner (`services/ingest/migrations.py`, transactional,
  tracked in `schema_migrations`) with `0001` baseline (spans,
  notifications — adopts legacy DBs) and `0002` control-plane tables:
  organizations, environments, agents, api_keys (hashed secrets),
  payments + append-only payment_transitions, decisions, reviews,
  policies + immutable policy_versions, and an append-only `audit_log`
  enforced by UPDATE/DELETE-blocking triggers. `IngestStore` runs
  pending migrations on open.
- Production configuration for the ingest service (PZ-009):
  `services/ingest/config.py` with fail-fast validated, immutable
  `Settings` loaded from env (`PAIZIQ_ENV`, `PAIZIQ_INGEST_DB`,
  `PAIZIQ_INGEST_KEYS`, request-limit and log-level overrides).
  `PAIZIQ_ENV=production` refuses in-memory storage and the dev key.
  Plus `.env.example` and a non-root `Dockerfile`.
- Machine-readable OpenAPI 3.1 specification for the ingest service
  (`services/ingest/openapi.json`) and generated stdlib-only client
  types (`paiziq.api_types`: `SpanIn`, `TraceBatch`, `NotificationIn`,
  …), regenerated via `make openapi` (PZ-008). Sync tests fail the gate
  if either artifact drifts from the live app; `paiziq.api_types` is
  re-exported from the top-level package.
- Canonical backend API contract (`docs/06_API_CONTRACT.md`) covering
  ingestion, decisions, payments, reviews, policies, organizations,
  environments, agents, API keys, and audit logs — envelope format,
  auth scopes, error codes, tenancy model, payment state machine, and
  per-endpoint implementation status (PZ-007). Referenced from README
  and `02_ARCHITECTURE.md` §6.
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

### Fixed
- CI ingest job now installs the SDK (routers import `paiziq` since
  PZ-017) and the SDK examples step runs `payment_agent.py`.

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
