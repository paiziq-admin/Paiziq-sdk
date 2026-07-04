"""Event search over indexed span events (PZ-080)."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from auth import require_read_key
from deps import get_db_connection, get_db_lock
from envelope import list_meta, ok

router = APIRouter(tags=["search"])


@router.get("/v1/search/events")
def search_events(
    api_key: str = Depends(require_read_key),
    q: Optional[str] = Query(default=None),
    trace_id: Optional[str] = Query(default=None),
    from_ms: Optional[int] = Query(default=None, ge=0),
    to_ms: Optional[int] = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    conn: sqlite3.Connection = Depends(get_db_connection),
    lock: threading.Lock = Depends(get_db_lock),
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if trace_id:
        clauses.append("se.trace_id = ?")
        params.append(trace_id)
    if from_ms is not None:
        clauses.append("se.at_ms >= ?")
        params.append(from_ms)
    if to_ms is not None:
        clauses.append("se.at_ms <= ?")
        params.append(to_ms)
    if q:
        clauses.append(
            "se.id IN (SELECT rowid FROM span_events_fts WHERE span_events_fts MATCH ?)"
        )
        params.append(q)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with lock:
        total = conn.execute(
            f"SELECT COUNT(*) FROM span_events se {where}", tuple(params)
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT se.trace_id, se.span_id, se.name, se.kind, se.payload_json, se.at_ms "
            f"FROM span_events se {where} ORDER BY se.at_ms DESC, se.id "
            f"LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        ).fetchall()
    items = [
        {
            "trace_id": r[0], "span_id": r[1], "name": r[2], "kind": r[3],
            "payload": json.loads(r[4]), "at_ms": r[5],
        }
        for r in rows
    ]
    return ok(items, meta=list_meta(total, limit, offset))
