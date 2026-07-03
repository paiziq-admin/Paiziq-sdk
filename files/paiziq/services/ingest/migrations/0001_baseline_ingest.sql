-- 0001: baseline ingest-plane tables (spans + notifications).
-- Replicates the pre-migration ad-hoc schema exactly, with IF NOT EXISTS
-- so databases created before the migration runner adopt it cleanly.

CREATE TABLE IF NOT EXISTS spans (
    span_id        TEXT PRIMARY KEY,
    trace_id       TEXT NOT NULL,
    name           TEXT NOT NULL,
    parent_span_id TEXT,
    start_ms       INTEGER,
    end_ms         INTEGER,
    status         TEXT,
    payload        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans (trace_id);

CREATE TABLE IF NOT EXISTS notifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    severity      TEXT NOT NULL,
    title         TEXT NOT NULL,
    message       TEXT,
    request_id    TEXT,
    risk_flags    TEXT,
    created_at_ms INTEGER
);
