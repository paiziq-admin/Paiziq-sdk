-- Execution authorization metadata complements the shared SDK execution ledger.
ALTER TABLE payments ADD COLUMN category TEXT NOT NULL DEFAULT 'general';
ALTER TABLE payments ADD COLUMN mandate_json TEXT;
ALTER TABLE payments ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE payment_idempotency (
    env_id TEXT NOT NULL REFERENCES environments(id),
    idempotency_key TEXT NOT NULL,
    payment_id TEXT NOT NULL REFERENCES payments(id),
    payload_digest TEXT NOT NULL,
    PRIMARY KEY (env_id, idempotency_key)
);
CREATE TABLE execution_bindings (
    payment_id TEXT PRIMARY KEY REFERENCES payments(id),
    org_id TEXT NOT NULL REFERENCES organizations(id),
    env_id TEXT NOT NULL REFERENCES environments(id),
    request_id TEXT NOT NULL,
    claim_token_hash TEXT NOT NULL,
    actor TEXT NOT NULL,
    policy_version INTEGER,
    request_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    UNIQUE (org_id, env_id, request_id)
);
CREATE TRIGGER execution_bindings_no_update BEFORE UPDATE ON execution_bindings
BEGIN SELECT RAISE(ABORT, 'execution_bindings is append-only'); END;
CREATE TRIGGER execution_bindings_no_delete BEFORE DELETE ON execution_bindings
BEGIN SELECT RAISE(ABORT, 'execution_bindings is append-only'); END;

CREATE TABLE decision_contexts (
    decision_id TEXT PRIMARY KEY REFERENCES decisions(id),
    request_digest TEXT NOT NULL,
    policy_digest TEXT NOT NULL,
    request_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    evaluated_at_ms INTEGER NOT NULL
);
CREATE TRIGGER decision_contexts_no_update BEFORE UPDATE ON decision_contexts
BEGIN SELECT RAISE(ABORT, 'decision_contexts is append-only'); END;
CREATE TRIGGER decision_contexts_no_delete BEFORE DELETE ON decision_contexts
BEGIN SELECT RAISE(ABORT, 'decision_contexts is append-only'); END;

-- Outbox consumers claim a unique event/endpoint publication atomically.
ALTER TABLE webhook_deliveries ADD COLUMN source_event_id TEXT;
CREATE UNIQUE INDEX idx_webhook_source_event
ON webhook_deliveries(endpoint_id, source_event_id)
WHERE source_event_id IS NOT NULL;
