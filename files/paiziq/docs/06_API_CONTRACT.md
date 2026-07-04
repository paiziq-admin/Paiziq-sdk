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

Key **scopes**:

| Scope | Grants |
| --- | --- |
| `ingest` | `POST /v1/traces`, `POST /v1/notifications` only |
| `read` | All `GET` endpoints |
| `admin` | Everything, including key lifecycle and policy publishing |

Keys resolve from two sources: bootstrap keys in the
`PAIZIQ_INGEST_KEYS` environment variable (full admin scope) and
database-backed scoped keys managed via the key lifecycle APIs (§6).
Missing key → `401`; invalid or insufficient-scope key → `403`.

### 1.3 Response envelope

**Control-plane** endpoints (everything except the two frozen ingest POSTs
and their read-backs) use a uniform envelope:

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

**Ingest-plane** endpoints keep their original (frozen) raw shapes, e.g.
`{"accepted": 3}` — the deployed SDK depends on them.

### 1.4 Error codes

| HTTP | `error.code` | Meaning |
| --- | --- | --- |
| 400 | `validation_error` | Body/query failed schema validation |
| 401 | `unauthorized` | Missing/malformed Authorization header |
| 403 | `forbidden` | Unknown key or insufficient scope |
| 404 | `not_found` | Resource does not exist |
| 409 | `conflict` | Duplicate create (idempotency/uniqueness) |
| 409 | `invalid_state_transition` | Payment/review state machine violation |
| 413 | `payload_too_large` | Body > 1 MB or batch > 500 spans |
| 422 | `validation_error` | Semantically invalid field values |
| 429 | `rate_limited` | Too many requests (PZ-083) |

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
| `GET /v1/payments` (filters: `env_id`, `agent_id`, `state`) | ✅ |
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
record.

## 9. Reviews

| Endpoint | Status |
| --- | --- |
| `GET /v1/reviews` (filters: `state`, `env_id`) | ⬜ (phase 3) |
| `GET /v1/reviews/{review_id}` | ⬜ (phase 3) |
| `POST /v1/reviews/{review_id}/approve` | ⬜ (phase 3) |
| `POST /v1/reviews/{review_id}/reject` | ⬜ (phase 3) |

Review: `{"id": "rev_…", "payment_id": "pay_…", "decision_id": "dec_…",
"state": "open" | "approved" | "rejected", "reviewer_id": str | null,
"note": str | null, "created_at_ms": 0, "resolved_at_ms": int | null}`.

`approve`/`reject` require `{"reviewer_id": str, "note": str?}`, are valid
only from `open` (`409` otherwise), and transition the underlying payment
(`needs_review → approved | rejected`). Resolutions fan out as signed
webhooks once PZ-077 lands.

## 10. Policies (PZ-022/023/024)

| Endpoint | Status |
| --- | --- |
| `GET /v1/policies` · `GET /v1/policies/{policy_id}` | ✅ |
| `POST /v1/policies` (create draft) | ✅ |
| `PUT /v1/policies/{policy_id}/draft` | ✅ |
| `POST /v1/policies/{policy_id}/publish` | ✅ |
| `POST /v1/policies/{policy_id}/rollback` | ⬜ (phase 5) |
| `GET /v1/policies/{policy_id}/versions` · `…/versions/{version}` | ✅ |
| `GET /v1/policies/{policy_id}/versions/compare` | ⬜ (phase 5) |
| `POST /v1/policies/simulate` | ⬜ (phase 5) |

Policy document mirrors the SDK `PaymentPolicy` fields (`review_threshold`,
`hard_limit`, `merchant_allowlist`, `merchant_blocklist`, `known_merchants`,
`treat_unknown_merchant_as`, `daily_budget`, `monthly_budget`,
`budget_warning_ratio`, `review_categories`, `allowed_currencies`,
`max_tx_per_hour`). Published
versions are immutable snapshots with monotonically increasing
`version`; exactly one version per environment is `active`. Rollback
publishes a *new* version whose content copies an older one — history is
never rewritten. `simulate` evaluates a hypothetical payment against a
draft or published version without persisting anything.

## 11. Audit logs (PZ-074/075)

| Endpoint | Status |
| --- | --- |
| `GET /v1/audit-logs` (filters: `actor`, `action`, `resource`, `from_ms`, `to_ms`) | ⬜ (phase 7) |

Entry: `{"id": "aud_…", "actor": "key_… | reviewer:jane", "action":
"payment.transition", "resource": "pay_…", "detail": {...}, "at_ms": 0}`.
Storage is **append-only** — the API exposes no update or delete, matching
the SDK audit-store invariant. Every sensitive control-plane mutation
(key lifecycle, policy publish/rollback, review resolution, payment
transition) writes an entry.

---

## 12. Contract change process

1. Endpoints marked ⬜ may be refined until their first implementation
   lands; from then on they follow the frozen-contract rules above.
2. Every implemented change updates, in the same PR: this document's
   status column, the generated OpenAPI spec, tests, `CHANGELOG.md`, and
   the progress tracker (`05_PROGRESS_TRACKER.md`).
3. The docs site (`docs/site/`) documents only ✅ endpoints.
