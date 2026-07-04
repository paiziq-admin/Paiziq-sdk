"""Append-only audit-log writer for control-plane mutations."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from ids import new_id, now_ms


class AuditLog:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def record(
        self,
        actor: str,
        action: str,
        resource: str,
        detail: Optional[dict[str, Any]] = None,
    ) -> str:
        audit_id = new_id("aud")
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (audit_id, actor, action, resource, detail, at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (audit_id, actor, action, resource, json.dumps(detail or {}), now_ms()),
            )
            self._conn.commit()
        return audit_id

    def entries_for(self, resource: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT audit_id, actor, action, resource, detail, at_ms "
                "FROM audit_log WHERE resource = ? ORDER BY id",
                (resource,),
            ).fetchall()
        return [
            {
                "id": r[0], "actor": r[1], "action": r[2], "resource": r[3],
                "detail": json.loads(r[4]), "at_ms": r[5],
            }
            for r in rows
        ]

    def list(
        self,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        resource: Optional[str] = None,
        from_ms: Optional[int] = None,
        to_ms: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if resource:
            clauses.append("resource = ?")
            params.append(resource)
        if from_ms is not None:
            clauses.append("at_ms >= ?")
            params.append(from_ms)
        if to_ms is not None:
            clauses.append("at_ms <= ?")
            params.append(to_ms)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM audit_log {where}", tuple(params)
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT audit_id, actor, action, resource, detail, at_ms FROM audit_log {where} "
                f"ORDER BY at_ms DESC, id LIMIT ? OFFSET ?",
                tuple(params) + (limit, offset),
            ).fetchall()
        items = [
            {
                "id": r[0], "actor": r[1], "action": r[2], "resource": r[3],
                "detail": json.loads(r[4]), "at_ms": r[5],
            }
            for r in rows
        ]
        return items, total
