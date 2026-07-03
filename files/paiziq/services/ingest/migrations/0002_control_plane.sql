-- 0002: control-plane domain tables per docs/06_API_CONTRACT.md.
-- Tenancy (organizations/environments), agents, API keys, payments with
-- state transitions, decisions, reviews, versioned policies, and an
-- append-only audit log (UPDATE/DELETE blocked by triggers).

CREATE TABLE IF NOT EXISTS organizations (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS environments (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('sandbox', 'production')),
    created_at_ms INTEGER NOT NULL,
    UNIQUE (org_id, name)
);

CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    env_id        TEXT NOT NULL REFERENCES environments(id),
    name          TEXT NOT NULL,
    framework     TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    metadata      TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL,
    UNIQUE (env_id, name)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
    env_id        TEXT NOT NULL REFERENCES environments(id),
    name          TEXT NOT NULL,
    scope         TEXT NOT NULL CHECK (scope IN ('ingest', 'read', 'admin')),
    secret_hash   TEXT NOT NULL UNIQUE,
    secret_prefix TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    rotated_at_ms INTEGER,
    revoked_at_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_api_keys_env ON api_keys (env_id);

CREATE TABLE IF NOT EXISTS payments (
    id                 TEXT PRIMARY KEY,
    env_id             TEXT NOT NULL REFERENCES environments(id),
    agent_id           TEXT NOT NULL REFERENCES agents(id),
    principal_id       TEXT NOT NULL,
    merchant           TEXT NOT NULL,
    amount             REAL NOT NULL CHECK (amount > 0),
    currency           TEXT NOT NULL DEFAULT 'USD',
    intent_description TEXT NOT NULL DEFAULT '',
    state              TEXT NOT NULL DEFAULT 'proposed' CHECK (
        state IN ('proposed', 'approved', 'needs_review', 'rejected', 'executed', 'failed')
    ),
    request_id         TEXT,
    idempotency_key    TEXT UNIQUE,
    created_at_ms      INTEGER NOT NULL,
    updated_at_ms      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_env_state ON payments (env_id, state);
CREATE INDEX IF NOT EXISTS idx_payments_agent ON payments (agent_id);

CREATE TABLE IF NOT EXISTS payment_transitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id TEXT NOT NULL REFERENCES payments(id),
    from_state TEXT NOT NULL,
    to_state   TEXT NOT NULL,
    actor      TEXT NOT NULL,
    reason     TEXT,
    at_ms      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transitions_payment ON payment_transitions (payment_id);

CREATE TABLE IF NOT EXISTS decisions (
    id             TEXT PRIMARY KEY,
    payment_id     TEXT NOT NULL REFERENCES payments(id),
    policy_version INTEGER,
    verdict        TEXT NOT NULL CHECK (verdict IN ('approved', 'needs_review', 'rejected')),
    reasons        TEXT NOT NULL DEFAULT '[]',
    risk_flags     TEXT NOT NULL DEFAULT '[]',
    created_at_ms  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_payment ON decisions (payment_id);

CREATE TABLE IF NOT EXISTS reviews (
    id             TEXT PRIMARY KEY,
    payment_id     TEXT NOT NULL REFERENCES payments(id),
    decision_id    TEXT NOT NULL REFERENCES decisions(id),
    state          TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'approved', 'rejected')),
    reviewer_id    TEXT,
    note           TEXT,
    created_at_ms  INTEGER NOT NULL,
    resolved_at_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_reviews_state ON reviews (state);

CREATE TABLE IF NOT EXISTS policies (
    id             TEXT PRIMARY KEY,
    env_id         TEXT NOT NULL REFERENCES environments(id),
    name           TEXT NOT NULL,
    draft_document TEXT,
    created_at_ms  INTEGER NOT NULL,
    UNIQUE (env_id, name)
);

CREATE TABLE IF NOT EXISTS policy_versions (
    policy_id       TEXT NOT NULL REFERENCES policies(id),
    version         INTEGER NOT NULL CHECK (version >= 1),
    document        TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    published_at_ms INTEGER NOT NULL,
    PRIMARY KEY (policy_id, version)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL UNIQUE,
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    resource TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT '{}',
    at_ms    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_log_resource ON audit_log (resource);
CREATE INDEX IF NOT EXISTS idx_audit_log_at ON audit_log (at_ms);

-- Append-only enforcement: the audit log can never be rewritten.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
