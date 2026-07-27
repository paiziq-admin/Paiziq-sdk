# Paiziq Backend API Contract — v1

**Version:** 1.0 · **Status:** Draft (living contract) · **Scope:** Backend HTTP API

This document is the canonical contract for the Paiziq backend HTTP API:
ingestion, decisions, payments, reviews, policies, organizations,
environments, agents, API keys, and audit logs. The wire-level summary in
`02_ARCHITECTURE.md` §6 remains the short-form reference; this document is
the authoritative long form. The machine-readable OpenAPI specification
(`services/ingest/openapi.json`) is generated from the implemented service
and must always match the endpoints marked ✅ here.

Legend (matches the progress tracker): ✅ implemented · 🔄 in progress ·
⬜ planned (target phase in parentheses).

---

## 1. Conventions

### 1.1 Base URL and versioning

All endpoints are rooted at `/v1`. The v1 wire contract for the two SDK
ingest endpoints (`POST /v1/traces`, `POST /v1/notifications`) is **frozen**:
their request/response shapes never change incompatibly. New capability
ships as new endpoints under `/v1`; breaking changes require `/v2`.

### 1.2 Authentication

Every endpoint except `GET /health` requires a Bearer API key:

```
Authorization: Bearer <key>
```

Database-backed keys carry a role that resolves to effective
capabilities:

| Role | Effective capabilities |
| --- | --- |
| `admin` | `ingest`, `read`, `review`, and `admin` |
| `developer` | `ingest` and `read` |
| `reviewer` | `read` and `review` |
| `read_only` | `read` |

Keys resolve from two sources: bootstrap keys in the
`PAIZIQ_INGEST_KEYS` environment variable (full admin scope) and
database-backed keys managed via the key lifecycle APIs (§6). The
persisted `scope` field remains `ingest` / `read` / `admin` for
backward compatibility; `role` controls the effective capabilities.
Missing key → `401`; invalid or insufficient-scope key → `403`.

### 1.3 Response envelope

Successful **control-plane** responses and domain errors (everything
except the two frozen ingest POSTs and their read-backs) use this
envelope:

```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "meta": { "total": 120, "limit": 50, "offset": 0 }
}
```

- `data` is `null` on error; `error` is `null` on success.
- `meta` appears only on paginated list responses.
- Errors: `error` is `{"code": "<machine_code>", "message": "<human text>"}`.
- Authentication/dependency failures and FastAPI request-schema
  validation currently retain the framework's raw `{"detail": ...}`
  shape; clients must accept both forms.

**Ingest-plane** endpoints keep their original (frozen) raw shapes, e.g.
`{"accepted": 3}` — the deployed SDK depends on them.

### 1.4 Error codes

Domain errors use the control-plane envelope:

| HTTP | `error.code` | Meaning |
| --- | --- | --- |
| 404 | `not_found` | Resource does not exist |
| 409 | `conflict` | Duplicate create (idempotency/uniqueness) |
| 409 | `invalid_state_transition` | Payment/review state machine violation |
| 409 | `review_resolution_required` | An open review must be resolved through the review API |
| 409 | `review_not_open` | Review action attempted after resolution |
| 409 | `review_assignment_conflict` | A different reviewer owns the item |
| 422 | `validation_error` | Semantically invalid field values |
| 429 | `rate_limited` | Too many requests (PZ-083) |

Framework/dependency failures use raw `{"detail": ...}` responses:
missing or malformed authorization → `401`; unknown keys or
insufficient capability → `403`; oversized ingest bodies/batches →
`413`; and request-schema validation → `422`.

### 1.5 Identifiers, timestamps, pagination

- IDs are opaque prefixed strings: `org_…`, `env_…`, `agt_…`, `key_…`,
  `pay_…`, `dec_…`, `rev_…`, `pol_…`, `aud_…`. Trace/span IDs are
  SDK-generated hex strings.
- Timestamps are integer **milliseconds since epoch** (`*_ms`), matching
  the SDK span format.
- List endpoints accept `limit` (default 50, max 200) and `offset`
  (default 0) and return `meta.total`.

### 1.6 Tenancy model

```
organization ──< environment (e.g. sandbox, production)
                     ├──< agent
                     ├──< api key
                     ├──< policy (versioned)
                     ├──< payment ──< decision ──< review
                     └──< audit log entries / traces / notifications
```

---

## 2. Service

| Endpoint | Status | Notes |
| --- | --- | --- |
| `GET /health` | ✅ | Liveness. `{"status": "ok"}`. No auth. |

---

## 3. Ingestion (frozen wire contract)

| Endpoint | Status |
| --- | --- |
| `POST /v1/traces` | ✅ |
| `GET /v1/traces/{trace_id}` | ✅ |
| `POST /v1/notifications` | ✅ |
| `GET /v1/notifications` | ✅ |

### 3.1 `POST /v1/traces`

Idempotent span upsert keyed by `span_id` (SDK retries are safe).

Request:

```json
{"spans": [{
  "name": "paiziq.review_payment",
  "trace_id": "tr…", "span_id": "sp…", "parent_span_id": null,
  "start_ms": 0, "end_ms": 0, "duration_ms": 0,
  "status": "ok",
  "attributes": {"paiziq.decision": "approved"},
  "events": [{"name": "decision", "ts_ms": 0, "attributes": {}}]
}]}
```

Limits: body ≤ 1 MB, ≤ 500 spans per batch (`413` beyond either).
Response: `{"accepted": <int>}`.

### 3.2 `GET /v1/traces/{trace_id}`

Response: `{"trace_id": "…", "spans": [<span>, …]}` ordered by `start_ms`.

### 3.3 `POST /v1/notifications`

Request: `{severity, title, message, request_id, risk_flags, created_at_ms}`.
Response: `{"status": "accepted"}`.

### 3.4 `GET /v1/notifications`

Response: `{"notifications": [<notification>, …]}`, newest first, ≤ 100.

---

## 4. Organizations and environments (PZ-011)

| Endpoint | Status |
| --- | --- |
| `POST /v1/orgs` | ✅ |
| `GET /v1/orgs` · `GET /v1/orgs/{org_id}` | ✅ |
| `POST /v1/orgs/{org_id}/environments` | ✅ |
| `GET /v1/orgs/{org_id}/environments` | ✅ |

Organization: `{"id": "org_…", "name": str, "created_at_ms": int}`.
`name` is unique (`409 conflict` on duplicate).

Environment: `{"id": "env_…", "org_id": "org_…", "name": str,
"kind": "sandbox" | "production", "created_at_ms": int}`.
`name` is unique within an organization.

Organizations and environments are never deleted in v1 (audit-trail
integrity); deactivation is a later phase.

## 5. Agents (PZ-012)

| Endpoint | Status |
| --- | --- |
| `POST /v1/agents` | ✅ |
| `GET /v1/agents` (filter: `env_id`) | ✅ |
| `GET /v1/agents/{agent_id}` | ✅ |
| `PATCH /v1/agents/{agent_id}` | ✅ |

Agent:

```json
{"id": "agt_…", "env_id": "env_…", "name": "procurement-agent",
 "framework": "langchain", "status": "active",
 "metadata": {"owner": "team-payments"}, "created_at_ms": 0}
```

`PATCH` accepts `name`, `status` (`active` | `disabled`), and `metadata`
(full replacement of the metadata object). Registration is idempotent on
(`env_id`, `name`): re-registering returns the existing agent (`200`, not
`409`), so agent processes can self-register at boot.

## 6. API keys (PZ-013)

| Endpoint | Status |
| --- | --- |
| `POST /v1/api-keys` | ✅ |
| `GET /v1/api-keys` (filter: `env_id`) | ✅ |
| `POST /v1/api-keys/{key_id}/rotate` | ✅ |
| `DELETE /v1/api-keys/{key_id}` (revoke) | ✅ |

- Keys are generated server-side (`pzq_<env-kind>_<random>`), stored
  **hashed** (SHA-256), and the plaintext is returned **exactly once** in
  the create/rotate response as `data.secret`. All other reads return the
  prefix only (`data.secret_prefix`, first 12 chars).
- `rotate` issues a new secret and starts a grace window during which the
  old secret still validates (`grace_seconds`, default 0 = immediate).
- Revocation is a soft delete: the row is kept (`revoked_at_ms` set) so
  audit logs stay resolvable; the key stops validating immediately.

## 7. Payments (PZ-016)

| Endpoint | Status |
| --- | --- |
| `POST /v1/payments` | ✅ |
| `GET /v1/payments` (server filters, sort, pagination) | ✅ |
| `GET /v1/payments/{payment_id}` | ✅ |
| `POST /v1/payments/{payment_id}/transition` | ✅ |

Payment proposal:

```json
{"id": "pay_…", "env_id": "env_…", "agent_id": "agt_…",
 "principal_id": "user-42", "merchant": "acme corp", "amount": 49.99,
 "currency": "USD", "intent_description": "Renew subscription",
 "state": "proposed", "request_id": "req_…",
 "created_at_ms": 0, "updated_at_ms": 0}
```

`GET /v1/payments` accepts optional `env_id`, `agent_id`, `state`,
`currency`, `min_amount`, `max_amount`, `q`, `from_ms`, and `to_ms`,
then applies `sort`, `limit`, and `offset`. Amount and creation-time
bounds are inclusive. `q` is a case-insensitive literal substring search
across payment ID, agent ID, principal ID, merchant, request ID, and
intent description. Supported sort values are `created_desc` (default),
`created_asc`, `amount_desc`, `amount_asc`, and `merchant_asc`; each has
an ID/time tie-breaker for deterministic pages. `meta.total` counts the
fully filtered result set. Reversed amount or time bounds return `422
validation_error`.

State machine (server-enforced; anything else → `409
invalid_state_transition`):

```
proposed ──► approved ──► executed
    │            │   └──► failed
    │            └ (no other exits)
    ├──► needs_review ──► approved | rejected
    └──► rejected (terminal)
```

`POST /v1/payments` supports the `Idempotency-Key` header: replays return
the original payment (`200`) instead of creating a duplicate. Transitions
record `{from, to, actor, reason, at_ms}` rows; history is append-only and
returned in `GET /v1/payments/{id}` as `data.transitions`.
When a `needs_review` payment still has an open review, the generic
transition endpoint rejects `approved` / `rejected` with `409
review_resolution_required`; callers must use §9.

## 8. Decisions (PZ-017)

| Endpoint | Status |
| --- | --- |
| `POST /v1/decisions` | ✅ |
| `GET /v1/decisions/{decision_id}` | ✅ |
| `GET /v1/decisions` (filter: `payment_id`) | ✅ |

`POST /v1/decisions` evaluates a persisted payment (`{"payment_id":
"pay_…"}`) against the environment's active policy using the deterministic
SDK engine (allow / review / block outcomes map to the SDK verdicts
`approved` / `needs_review` / `rejected`). Evaluating also applies the
corresponding payment state transition and, for `needs_review`, opens a
review (§9).

Decision:

```json
{"id": "dec_…", "payment_id": "pay_…", "policy_version": 3,
 "verdict": "approved" | "needs_review" | "rejected",
 "reasons": ["All decision rules passed"],
 "risk_flags": [], "created_at_ms": 0}
```

Decisions are immutable; re-evaluating a payment creates a new decision
record. Only `proposed` and `needs_review` payments are evaluable. If a
`needs_review` re-evaluation again yields `needs_review`, the service
reuses the canonical open review and updates its `decision_id` rather
than opening another row. If it yields `approved` or `rejected` while an
open review exists, the request returns `409
review_resolution_required` before creating the decision or changing
the payment.

## 9. Reviews (PZ-101)

| Endpoint | Status |
| --- | --- |
| `GET /v1/reviews` (`state`, `env_id`, `reviewer_id`, `priority`) | ✅ |
| `GET /v1/reviews/identity` | ✅ |
| `GET /v1/reviews/{review_id}` | ✅ |
| `POST /v1/reviews/{review_id}/claim` | ✅ |
| `POST /v1/reviews/{review_id}/release` | ✅ |
| `POST /v1/reviews/{review_id}/reassign` | ✅ |
| `POST /v1/reviews/{review_id}/request-more-info` | ✅ |
| `POST /v1/reviews/{review_id}/escalate` | ✅ |
| `POST /v1/reviews/{review_id}/approve` | ✅ |
| `POST /v1/reviews/{review_id}/decline` | ✅ |
| `POST /v1/reviews/{review_id}/reject` (compatibility alias) | ✅ |

Queue reads accept the standard `limit` / `offset` pagination
parameters. Open items are sorted by priority (`urgent` first), then
SLA deadline. Each item embeds its payment and includes:

```json
{
  "id": "rev_…",
  "payment_id": "pay_…",
  "decision_id": "dec_…",
  "state": "open",
  "reviewer_id": null,
  "note": null,
  "priority": "normal",
  "last_action": "opened",
  "created_at_ms": 0,
  "updated_at_ms": 0,
  "assigned_at_ms": null,
  "resolved_at_ms": null,
  "sla_deadline_ms": 0,
  "sla_remaining_ms": 0,
  "sla_breached": false,
  "payment": {"id": "pay_…", "state": "needs_review"}
}
```

`GET /v1/reviews/identity` returns:

```json
{
  "reviewer_id": "payments-reviewer",
  "role": "reviewer",
  "env_id": "env_…",
  "managed_identity": true
}
```

For a database-managed key, review queue/detail/action access is limited
to the key's `env_id`, and `reviewer_id` is the API-key name. Queue
requests for another environment and cross-environment review detail or
mutations return `403`. Bootstrap admin keys are unscoped and return
`reviewer_id: null`, `env_id: null`, and `managed_identity: false`.

Review mutations require the `review` capability (`reviewer`) or
`admin`; `developer` and `read_only` keys can read but cannot act. For a
managed key, `claim`, `release`, request-info, escalation, approve, and
decline bodies must name the authenticated key's exact `reviewer_id`.
`claim` rejects ownership by another reviewer. `release` accepts the same
body plus an optional `note`; `reassign` requires a target
`reviewer_id` and a nonblank `note`. The authenticated key remains the
actor, and a non-admin caller must own the review; admin may override
ownership.

`request-more-info` requires `{"reviewer_id": str, "note": str}` and
keeps the review open. `escalate` additionally accepts `priority:
"high" | "urgent"` and keeps the review open. `approve`, `decline`, and
the `reject` alias all require a non-blank note, are valid only from
`open`, and require the underlying payment to remain `needs_review`.
Review terminal state, payment terminal state, and the append-only
payment transition commit in one SQLite transaction or roll back
together. Every workflow mutation writes an audit entry and fans out a
signed `review.*` webhook event after the store mutation.

Migration `0008_review_workflows.sql` extends existing review rows with
priority/action/assignment/update fields, backfills `updated_at_ms` from
`created_at_ms`, and adds reviewer/state, priority/SLA, and
payment/state indexes. The migration runner applies it once,
transactionally, when the database opens; a failure rolls back and
aborts startup.

## 10. Policies (PZ-022/023/024)

| Endpoint | Status |
| --- | --- |
| `GET /v1/policies` · `GET /v1/policies/{policy_id}` | ✅ |
| `POST /v1/policies` (create draft) | ✅ |
| `PUT /v1/policies/{policy_id}/draft` | ✅ |
| `POST /v1/policies/{policy_id}/publish` | ✅ |
| `POST /v1/policies/{policy_id}/rollback` | ✅ |
| `GET /v1/policies/{policy_id}/versions` · `…/versions/{version}` | ✅ |
| `GET /v1/policies/{policy_id}/versions/compare` (`base`/`target`: version or `draft`) | ✅ |
| `POST /v1/policies/simulate` | ✅ |

Policy document mirrors the SDK `PaymentPolicy` fields (`review_threshold`,
`hard_limit`, `merchant_allowlist`, `merchant_blocklist`, `known_merchants`,
`treat_unknown_merchant_as`, `daily_budget`, `monthly_budget`,
`budget_warning_ratio`, `review_categories`, `allowed_currencies`,
`max_tx_per_hour`). Published
versions are immutable snapshots with monotonically increasing
`version`; exactly one version per environment is `active`. Rollback
publishes a *new* version whose content copies an older one and re-syncs
the draft to match — history is never rewritten. Compare takes `base`
and `target` query refs (a version number or the literal `draft`) and
returns only changed fields as `{field: {base, target}}`. `simulate`
evaluates a hypothetical payment (`{payment, document? | policy_id? +
use_draft?/version?, env_id?}`) without persisting anything; precedence
is inline document > draft > specific version > latest published >
environment's active version > engine default, and the response carries
`{verdict, reasons, risk_flags, policy_source, persisted: false}`.

Draft update accepts `{"document": {...}, "reason"?: str}`. If supplied,
`reason` must be nonblank and is recorded in the append-only
`policy.draft_update` audit detail. An inline simulator `document` takes
precedence over stored drafts/versions, allowing clients to evaluate an
unsaved document without persisting it.

## 11. Audit logs (PZ-074/075)

| Endpoint | Status |
| --- | --- |
| `GET /v1/audit-logs` (filters: `actor`, `action`, `resource`, `from_ms`, `to_ms`) | ✅ |

Entry: `{"id": "aud_…", "actor": "key:key_…", "action":
"review.approved", "resource": "rev_…", "detail": {...}, "at_ms": 0}`.
Context-aware managed-review actions use the database key ID; bootstrap
and secret-only dependencies use a stable
`key:bootstrap:<fingerprint>` actor instead.
Storage is **append-only** — the API exposes no update or delete, matching
the SDK audit-store invariant. Every sensitive control-plane mutation
(key lifecycle, policy draft/publish/rollback, review resolution, payment
transition) writes an entry.

## 12. Webhooks (PZ-076/077/078) ✅

| Endpoint | Status |
| --- | --- |
| `POST /v1/webhook-endpoints` | ✅ |
| `GET /v1/webhook-endpoints` | ✅ |
| `PATCH /v1/webhook-endpoints/{id}` | ✅ |
| `GET /v1/webhook-deliveries` (`endpoint_id`, `state`, `env_id`, `event_type`, `payment_id`, `review_id`) | ✅ |
| `GET /v1/webhook-deliveries/{id}` | ✅ |

Outbound events (`decision.created`, `payment.updated`,
`review.assigned`, `review.claimed`, `review.released`,
`review.reassigned`, `review.requested_info`, `review.escalated`,
`review.approved`, `review.rejected`, and `review.sla_breached`) are
HMAC-SHA256 signed (`Paiziq-Signature: t=...,v1=...`) and delivered with
exponential backoff (dead-letter after 5 attempts).

Delivery-list filters are server-side and compose before `limit` /
`offset`; `meta.total` is the exact filtered count. `payment_id` and
`review_id` compare against the exact values at
`payload.data.payment_id` and `payload.data.review_id`, respectively,
rather than doing client-side payload substring matching.

## 13. Metrics & search (PZ-079/080) ✅

| Endpoint | Status |
| --- | --- |
| `GET /v1/metrics/summary` | ✅ |
| `GET /v1/metrics/timeseries` | ✅ |
| `GET /v1/search/events` | ✅ |

`metrics/summary` requires `env_id` and accepts `from_ms` / `to_ms`.
`metrics/timeseries` requires `env_id` and `metric`, accepts `interval`
(`1h` or `1d`, default `1h`) plus the same time bounds. The summary
returns grouped `decisions`, `risk_flags`, and payment-state counts plus
open-review and webhook-delivery totals/rate. Timeseries supports
`decisions.<verdict>`, `payments.<state>`, and `payments.total`; the
latter counts every payment created in each bucket. Event search accepts
`q`, `trace_id`, `from_ms`, `to_ms`, `limit`, and `offset`.

## 14. Admin & audit read (PZ-074/081) ✅

| Endpoint | Status |
| --- | --- |
| `GET /v1/audit-logs` | ✅ (read capability; all roles) |
| `POST /v1/admin/retention/run` | ✅ (admin) |

Audit log listing (§11) is now implemented. RBAC roles on API keys: `admin`,
`developer`, `reviewer`, `read_only` (PZ-073). Rate limiting returns `429
rate_limited` (PZ-083).

## 15. Contract change process

1. Endpoints marked ⬜ may be refined until their first implementation
   lands; from then on they follow the frozen-contract rules above.
2. Every implemented change updates, in the same PR: this document's
   status column, the generated OpenAPI spec, tests, `CHANGELOG.md`, and
   the progress tracker (`05_PROGRESS_TRACKER.md`).
3. The docs site (`docs/site/`) documents only ✅ endpoints.
