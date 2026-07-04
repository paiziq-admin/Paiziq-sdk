-- 0004: outbound webhook delivery engine (PZ-076/PZ-077/078).
-- Endpoints are per-environment subscriber URLs with a signing secret
-- (encrypted at rest when PAIZIQ_SECRETS_KEY is set — PZ-082).
-- Deliveries are the retry queue; dead after max attempts (DLQ).
-- Every attempt is logged.

ALTER TABLE reviews ADD COLUMN sla_deadline_ms INTEGER;

CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id            TEXT PRIMARY KEY,
    env_id        TEXT NOT NULL REFERENCES environments(id),
    url           TEXT NOT NULL,
    secret        TEXT NOT NULL,
    events        TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_env ON webhook_endpoints (env_id);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              TEXT PRIMARY KEY,
    endpoint_id     TEXT NOT NULL REFERENCES webhook_endpoints(id),
    event_type      TEXT NOT NULL,
    payload         TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'pending'
                    CHECK (state IN ('pending', 'delivered', 'dead')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_ms INTEGER NOT NULL,
    last_error      TEXT,
    created_at_ms   INTEGER NOT NULL,
    updated_at_ms   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due
    ON webhook_deliveries (state, next_attempt_ms);

CREATE TABLE IF NOT EXISTS webhook_delivery_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL REFERENCES webhook_deliveries(id),
    attempt     INTEGER NOT NULL,
    status_code INTEGER,
    error       TEXT,
    at_ms       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_delivery
    ON webhook_delivery_logs (delivery_id);
