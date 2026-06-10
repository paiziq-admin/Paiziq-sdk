"""Postgres-backed append-only audit store.

Implements the `AuditStore` protocol with a single append-only table:

    CREATE TABLE IF NOT EXISTS paiziq_audit_records (
        record_id      TEXT PRIMARY KEY,
        event_type     TEXT NOT NULL,
        request_id     TEXT NOT NULL,
        trace_id       TEXT,
        payload        TEXT NOT NULL,          -- JSON document
        recorded_at_ms BIGINT NOT NULL
    );
    CREATE INDEX ... ON paiziq_audit_records (request_id);
    CREATE INDEX ... ON paiziq_audit_records (trace_id);

The store only ever INSERTs and SELECTs — immutability is a contract,
enforced operationally by granting the SDK role INSERT/SELECT only.

The connection is injected (anything DB-API 2.0 shaped works), so unit
tests use an in-memory sqlite database and `psycopg` is only required
when connecting by DSN: `pip install paiziq[postgres]`.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional, Protocol

from ..models import AuditRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paiziq_audit_records (
    record_id      TEXT PRIMARY KEY,
    event_type     TEXT NOT NULL,
    request_id     TEXT NOT NULL,
    trace_id       TEXT,
    payload        TEXT NOT NULL,
    recorded_at_ms BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paiziq_audit_request_id
    ON paiziq_audit_records (request_id);
CREATE INDEX IF NOT EXISTS idx_paiziq_audit_trace_id
    ON paiziq_audit_records (trace_id);
"""

_INSERT = (
    "INSERT INTO paiziq_audit_records "
    "(record_id, event_type, request_id, trace_id, payload, recorded_at_ms) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)

_SELECT_ALL = (
    "SELECT record_id, event_type, request_id, trace_id, payload, recorded_at_ms "
    "FROM paiziq_audit_records ORDER BY recorded_at_ms DESC, record_id DESC LIMIT %s"
)

_SELECT_BY_REQUEST = (
    "SELECT record_id, event_type, request_id, trace_id, payload, recorded_at_ms "
    "FROM paiziq_audit_records WHERE request_id = %s "
    "ORDER BY recorded_at_ms DESC, record_id DESC LIMIT %s"
)


class _ConnectionLike(Protocol):
    """Minimal DB-API 2.0 surface used by PostgresAuditStore."""

    def cursor(self) -> Any: ...
    def commit(self) -> None: ...


class PostgresAuditStore:
    """Append-only audit trail in PostgreSQL for production deployments."""

    def __init__(
        self,
        connection: Optional[_ConnectionLike] = None,
        dsn: Optional[str] = None,
        ensure_schema: bool = True,
        paramstyle: str = "%s",
    ) -> None:
        if connection is None:
            if dsn is None:
                raise ValueError("PostgresAuditStore requires either a connection or a dsn")
            try:
                import psycopg  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - install-time guidance
                raise ImportError(
                    "PostgresAuditStore by DSN requires the psycopg package. "
                    "Install it with: pip install 'paiziq[postgres]'"
                ) from exc
            connection = psycopg.connect(dsn)
        self._conn = connection
        self._lock = threading.Lock()
        # psycopg uses '%s' placeholders; sqlite (used in tests) uses '?'
        self._param = paramstyle
        if ensure_schema:
            self._ensure_schema()

    def _sql(self, statement: str) -> str:
        return statement.replace("%s", self._param)

    def _ensure_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            for stmt in _SCHEMA.strip().split(";"):
                if stmt.strip():
                    cur.execute(stmt)
            self._conn.commit()

    # ── AuditStore protocol ──────────────────────────────────────────────

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                self._sql(_INSERT),
                (
                    record.record_id,
                    record.event_type,
                    record.request_id,
                    record.trace_id,
                    json.dumps(record.payload, default=str),
                    record.recorded_at_ms,
                ),
            )
            self._conn.commit()

    def query(self, request_id: Optional[str] = None, limit: int = 100) -> list[AuditRecord]:
        with self._lock:
            cur = self._conn.cursor()
            if request_id is None:
                cur.execute(self._sql(_SELECT_ALL), (limit,))
            else:
                cur.execute(self._sql(_SELECT_BY_REQUEST), (request_id, limit))
            rows = cur.fetchall()

        records = [
            AuditRecord(
                record_id=row[0],
                event_type=row[1],
                request_id=row[2],
                trace_id=row[3],
                payload=json.loads(row[4]) if row[4] else {},
                recorded_at_ms=row[5],
            )
            for row in rows
        ]
        # Match the in-memory/JSONL stores: newest last.
        return list(reversed(records))
