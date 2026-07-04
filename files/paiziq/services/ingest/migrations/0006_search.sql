-- 0006: event search indexing (PZ-080).

CREATE TABLE IF NOT EXISTS span_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT NOT NULL,
    span_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'event',
    payload_json TEXT NOT NULL DEFAULT '{}',
    at_ms        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_span_events_trace ON span_events (trace_id);
CREATE INDEX IF NOT EXISTS idx_span_events_at ON span_events (at_ms);

CREATE VIRTUAL TABLE IF NOT EXISTS span_events_fts USING fts5(
    name,
    payload_json,
    content='span_events',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS span_events_ai AFTER INSERT ON span_events BEGIN
    INSERT INTO span_events_fts(rowid, name, payload_json)
    VALUES (new.id, new.name, new.payload_json);
END;

CREATE TRIGGER IF NOT EXISTS span_events_ad AFTER DELETE ON span_events BEGIN
    INSERT INTO span_events_fts(span_events_fts, rowid, name, payload_json)
    VALUES ('delete', old.id, old.name, old.payload_json);
END;

CREATE TRIGGER IF NOT EXISTS span_events_au AFTER UPDATE ON span_events BEGIN
    INSERT INTO span_events_fts(span_events_fts, rowid, name, payload_json)
    VALUES ('delete', old.id, old.name, old.payload_json);
    INSERT INTO span_events_fts(rowid, name, payload_json)
    VALUES (new.id, new.name, new.payload_json);
END;
