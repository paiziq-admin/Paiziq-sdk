"""Append-only audit-log writer for control-plane mutations.

Every sensitive backend action records who did what to which resource
(docs/06_API_CONTRACT.md §11). The audit_log table forbids UPDATE and
DELETE via triggers (migration 0002), so this writer only appends.
"""

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
