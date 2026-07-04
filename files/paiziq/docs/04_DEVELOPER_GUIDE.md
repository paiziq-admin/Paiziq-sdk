# Paiziq Developer Guide

Audience: engineers contributing to the Paiziq SDK or the ingest
service. Read `01_EXECUTIVE_SCOPE.md` for the why, `02_ARCHITECTURE.md`
for the how, `03_BUILD_DEPLOY_PLAN.md` for the when. This guide covers
day-to-day development. Implementation status lives in
`05_PROGRESS_TRACKER.md`; per-version change history in `../CHANGELOG.md`.

## 1. Repository layout

```
paiziq/
├── Makefile                  # all automated commands (make help)
├── CHANGELOG.md              # versioned, per-change history
├── docs/                     # scope, architecture, plan, guide, tracker
├── sdk/                      # the pip-installable `paiziq` package
│   ├── pyproject.toml        # version, extras: redis/postgres/dev/...
│   ├── src/paiziq/
│   │   ├── models.py         # PaymentRequest, Mandate, AuditRecord...
│   │   ├── sdk.py            # PaiziqSDK facade (review/execute)
│   │   ├── engine/           # decision rules, policy, budget stores
│   │   ├── audit/            # audit stores + payment gateways
│   │   ├── tracing/          # tracer, exporters, PII scrubbing
│   │   ├── transport.py      # sync/async HTTP + RetryPolicy backoff
│   │   ├── logging.py        # structured logging, debug(), redaction
│   │   └── webhooks.py       # HMAC webhook signature verification
│   ├── tests/                # pytest suite (unit, property, concurrency)
│   └── examples/             # runnable end-to-end examples
└── services/ingest/          # FastAPI ingest + control plane service
    ├── app.py                # endpoints, auth, limits
    ├── routers/              # orgs, agents, keys, payments, decisions
    ├── stores/               # per-entity SQLite stores
    ├── config.py             # env-driven settings, fail-fast validation
    ├── storage.py            # SQLite IngestStore (swap for RDS later)
    ├── migrations.py         # versioned schema migration runner
    ├── migrations/           # numbered .sql migrations (0001_, 0002_, ...)
    ├── openapi.json          # committed OpenAPI 3.1 spec (make openapi)
    ├── scripts/              # spec export + client-type generation
    └── tests/
```

Schema changes ship as a new numbered file in
`services/ingest/migrations/` — never edit an applied migration.
Migrations run automatically when the service opens its database.

## 2. Getting started

```bash
make venv          # python3 -m venv sdk/.venv
make install       # editable install with dev extras
make test          # SDK test suite
make ingest-install ingest-test   # ingest service deps + tests
```

Everything is automated through the Makefile; run `make help` to list
every target. CI (`.github/workflows/ci.yml`) runs the same targets'
commands on every push and pull request.

## 3. Daily workflow

1. Branch from `main`.
2. Make your change. Follow the architecture invariants below.
   If you change ingest endpoints or request/response models, run
   `make openapi` to regenerate `services/ingest/openapi.json` and
   `sdk/src/paiziq/api_types.py` (sync tests fail otherwise), and
   update `docs/06_API_CONTRACT.md`.
3. `make check` — lint, SDK tests, ingest tests, examples.
4. Add a bullet under the `Unreleased`/next version heading in
   `CHANGELOG.md` describing the change.
5. Update `docs/05_PROGRESS_TRACKER.md` if your change completes or
   starts a planned work item.
6. Open a PR; CI must be green.

## 4. Architecture invariants (do not break)

- **Zero-dependency core.** `sdk/src/paiziq` imports only the standard
  library at module import time. Integrations with optional packages
  (redis, psycopg, httpx, frameworks) must lazy-import inside
  functions/constructors and be exposed as pip extras.
- **Deterministic decisions.** Every rule verdict carries
  machine-readable `reasons`. No nondeterministic input (LLM calls,
  wall-clock randomness) inside `engine/`.
- **Observability never breaks the agent.** Exporters and scrubbers
  swallow and log their own failures.
- **Append-only audit.** Audit stores expose `append` and queries; no
  update or delete operations.
- **Enforcement at the tool boundary.** Framework wrappers gate the
  payment tool call; they do not try to police the model's text.

## 5. Versioning and documentation rules

- Semantic versioning. The single source of truth is
  `sdk/pyproject.toml`; mirror it in `paiziq.__version__`.
- Every behavior change lands with: code + tests + a CHANGELOG entry
  under the target version + tracker update.
- New public API must be re-exported from the top-level `paiziq`
  package and covered by at least one example or test.

## 6. Testing conventions

- Unit tests live next to the feature area (`test_engine.py`,
  `test_tracing.py`, `test_production_stores.py`, ...).
- Production-store tests use fakes (FakeRedis, SQLite as DB-API) — no
  live services needed; `make test` must pass offline.
- Property-based tests (Hypothesis) guard rule totality; concurrency
  tests guard the budget ledger and exporters.
- Ingest service tests use FastAPI's TestClient and verify the wire
  contract against the SDK's `Span.to_dict()` output.

## 7. Running the ingest service

```bash
make ingest-run        # uvicorn on http://127.0.0.1:8800
```

Configuration is environment-driven and validated fail-fast at startup
(`services/ingest/config.py`; see `.env.example` for every variable).
Auth uses Bearer API keys from `PAIZIQ_INGEST_KEYS` (comma-separated;
defaults to `dev-key` for local development). Storage defaults to
in-memory SQLite; set `PAIZIQ_INGEST_DB=/path.db` to persist. Setting
`PAIZIQ_ENV=production` refuses to boot with the dev key or an
in-memory database. A production container image is defined in
`services/ingest/Dockerfile`.

```bash
curl -s -X POST http://127.0.0.1:8800/v1/traces \
  -H 'Authorization: Bearer dev-key' -H 'Content-Type: application/json' \
  -d '{"spans": [{"name": "paiziq.review_payment", "trace_id": "tr1", "span_id": "s1"}]}'
```

## 8. The paiziq CLI

The SDK ships a stdlib-only CLI (`paiziq`, installed with the package;
see `sdk/src/paiziq/cli/`). Configuration lives in
`~/.paiziq/config.json` (override with `PAIZIQ_CONFIG_DIR`); the file
is chmod 0600 because it stores the API key.

```bash
paiziq init --endpoint http://127.0.0.1:8800   # write config
paiziq login --api-key <key>                   # verify + store the key
paiziq agents list                             # registry reads
paiziq agents register --name my-agent --env <env_id>
paiziq keys create --name ci --scope ingest --env <env_id>  # secret shown once
paiziq keys rotate <key_id> --grace-seconds 300
paiziq keys revoke <key_id>
paiziq dashboard deploy --dir paiziq-dashboard # static local dashboard
paiziq dashboard serve --port 8900             # serves + proxies /api/*
paiziq replay <trace_id>                       # pretty-print a span tree
```

`dashboard serve` proxies API reads server-side so the key never
reaches the browser; the hosted dashboard remains a separate
workstream.

## 9. Releasing

1. Bump the version in `sdk/pyproject.toml` and `paiziq/__init__.py`
   (they must match — `tests/test_version.py` enforces it).
2. Move CHANGELOG entries under the new version heading with the date.
3. `make check && make build` — artifacts land in `sdk/dist/`.
4. Tag `v<version>` and push the tag. The release workflow
   (`.github/workflows/release.yml`) verifies the tag matches the
   package version, re-runs the SDK suite, builds sdist+wheel, attaches
   them to a GitHub release, and publishes to PyPI when the
   `PYPI_API_TOKEN` repository secret is configured (the publish step
   skips cleanly when it is not).

### Ingest security env vars (PZ-073–084)

| Variable | Purpose |
| --- | --- |
| `PAIZIQ_RATE_LIMIT_RPM` | Requests/minute per API key (default 600 dev, 120 prod) |
| `PAIZIQ_CORS_ORIGINS` | Comma-separated allowed origins (empty = disabled) |
| `PAIZIQ_SECRETS_KEY` | Master key for Fernet encryption of webhook secrets |
| `PAIZIQ_REVIEW_SLA_MS` | Open-review SLA deadline (default 24h) |
| `PAIZIQ_RETENTION_*_DAYS` | Optional span/notification/audit retention |
