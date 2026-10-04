-- Shared SDK execution schema. Keep in sync with paiziq.execution.LEDGER_SCHEMA.
CREATE TABLE IF NOT EXISTS execution_records (
        execution_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
        org_id TEXT NOT NULL, env_id TEXT NOT NULL, agent_id TEXT NOT NULL,
        currency TEXT NOT NULL, amount TEXT NOT NULL, decision_id TEXT NOT NULL,
        request_digest TEXT NOT NULL, policy_digest TEXT NOT NULL,
        context_digest TEXT NOT NULL, provider_idempotency_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL, gateway_reference TEXT, error TEXT,
        created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL,
        confirmed_at_ms INTEGER, request_json TEXT NOT NULL, context_json TEXT NOT NULL,
        UNIQUE(org_id, env_id, request_id));

CREATE INDEX IF NOT EXISTS execution_budget_scope ON execution_records
        (org_id, env_id, agent_id, currency, status);

CREATE TABLE IF NOT EXISTS execution_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
        org_id TEXT NOT NULL, env_id TEXT NOT NULL, request_id TEXT NOT NULL,
        event_type TEXT NOT NULL, recorded_at_ms INTEGER NOT NULL, payload_json TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS execution_outbox_ack (
        event_id TEXT NOT NULL, consumer TEXT NOT NULL, acknowledged_at_ms INTEGER NOT NULL,
        PRIMARY KEY(event_id, consumer));

CREATE TABLE IF NOT EXISTS execution_reviews (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL,
        env_id TEXT NOT NULL, request_id TEXT NOT NULL, decision_id TEXT NOT NULL,
        request_digest TEXT NOT NULL, policy_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL, UNIQUE(org_id, env_id, decision_id));

CREATE TABLE IF NOT EXISTS execution_approvals (
        org_id TEXT NOT NULL, env_id TEXT NOT NULL, decision_id TEXT NOT NULL,
        reviewer_id TEXT NOT NULL, approved_at_ms INTEGER NOT NULL,
        PRIMARY KEY(org_id, env_id, decision_id));

CREATE TRIGGER IF NOT EXISTS execution_events_no_update BEFORE UPDATE ON execution_events BEGIN SELECT RAISE(ABORT, 'execution evidence is append-only'); END;

CREATE TRIGGER IF NOT EXISTS execution_events_no_delete BEFORE DELETE ON execution_events BEGIN SELECT RAISE(ABORT, 'execution evidence is append-only'); END;

CREATE TRIGGER IF NOT EXISTS execution_reviews_no_update BEFORE UPDATE ON execution_reviews BEGIN SELECT RAISE(ABORT, 'execution evidence is append-only'); END;

CREATE TRIGGER IF NOT EXISTS execution_reviews_no_delete BEFORE DELETE ON execution_reviews BEGIN SELECT RAISE(ABORT, 'execution evidence is append-only'); END;

CREATE TRIGGER IF NOT EXISTS execution_approvals_no_update BEFORE UPDATE ON execution_approvals BEGIN SELECT RAISE(ABORT, 'execution evidence is append-only'); END;

CREATE TRIGGER IF NOT EXISTS execution_approvals_no_delete BEFORE DELETE ON execution_approvals BEGIN SELECT RAISE(ABORT, 'execution evidence is append-only'); END;

CREATE TRIGGER IF NOT EXISTS execution_outbox_ack_no_update BEFORE UPDATE ON execution_outbox_ack BEGIN SELECT RAISE(ABORT, 'execution evidence is append-only'); END;

CREATE TRIGGER IF NOT EXISTS execution_outbox_ack_no_delete BEFORE DELETE ON execution_outbox_ack BEGIN SELECT RAISE(ABORT, 'execution evidence is append-only'); END;

CREATE TRIGGER IF NOT EXISTS execution_records_evidence_immutable BEFORE UPDATE OF execution_id,request_id,org_id,env_id,agent_id,currency,amount,decision_id,request_digest,policy_digest,context_digest,provider_idempotency_key,created_at_ms,request_json,context_json ON execution_records BEGIN SELECT RAISE(ABORT, 'execution authorization is immutable'); END;

CREATE TRIGGER IF NOT EXISTS execution_records_no_delete BEFORE DELETE ON execution_records BEGIN SELECT RAISE(ABORT, 'execution records cannot be deleted'); END;
