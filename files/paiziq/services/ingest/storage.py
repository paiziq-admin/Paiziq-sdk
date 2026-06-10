"""SQLite storage for the ingest service.

Spans are upserted by span_id so redelivered batches (SDK retries) are
idempotent. Notifications are append-only. SQLite keeps the minimum
viable cloud footprint local-friendly; the Phase 1 production target
swaps this module for RDS PostgreSQL behind the same functions.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

_SCHEMA = """
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
"""

_UPSERT_SPAN = """
INSERT INTO spans (span_id, trace_id, name, parent_span_id, start_ms, end_ms, status, payload)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(span_id) DO UPDATE SET
    trace_id=excluded.trace_id, name=excluded.name,
    parent_span_id=excluded.parent_span_id, start_ms=excluded.start_ms,
    end_ms=excluded.end_ms, status=excluded.status, payload=excluded.payload
"""


class IngestStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def upsert_spans(self, spans: list[dict[str, Any]]) -> int:
        rows = [
            (
                s["span_id"], s["trace_id"], s.get("name", ""), s.get("parent_span_id"),
                s.get("start_ms"), s.get("end_ms"), s.get("status"),
                json.dumps(s, default=str),
            )
            for s in spans
        ]
        with self._lock:
            self._conn.executemany(_UPSERT_SPAN, rows)
            self._conn.commit()
        return len(rows)

    def spans_for_trace(self, trace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT payload FROM spans WHERE trace_id = ? ORDER BY start_ms", (trace_id,)
            )
            return [json.loads(row[0]) for row in cur.fetchall()]

    def span_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]

    def add_notification(self, n: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO notifications (severity, title, message, request_id, risk_flags, created_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    n["severity"], n["title"], n.get("message"), n.get("request_id"),
                    json.dumps(n.get("risk_flags", [])), n.get("created_at_ms"),
                ),
            )
            self._conn.commit()

    def notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT severity, title, message, request_id, risk_flags, created_at_ms "
                "FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
        return [
            {
                "severity": r[0], "title": r[1], "message": r[2],
                "request_id": r[3], "risk_flags": json.loads(r[4] or "[]"),
                "created_at_ms": r[5],
            }
            for r in rows
        ]
