"""Agent registration and metadata persistence (contract §5).

Registration is idempotent on (env_id, name) so agent processes can
self-register at boot; metadata updates replace the whole object.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from ids import new_id, now_ms

_COLS = "id, env_id, name, framework, status, metadata, created_at_ms"


def _row(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0], "env_id": row[1], "name": row[2], "framework": row[3],
        "status": row[4], "metadata": json.loads(row[5]), "created_at_ms": row[6],
    }


class AgentStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def register(
        self,
        env_id: str,
        name: str,
        framework: Optional[str],
        metadata: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Create or return the existing agent; True when newly created."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLS} FROM agents WHERE env_id = ? AND name = ?", (env_id, name)
            ).fetchone()
            if row:
                return _row(row), False
            agent = {
                "id": new_id("agt"), "env_id": env_id, "name": name,
                "framework": framework, "status": "active",
                "metadata": metadata, "created_at_ms": now_ms(),
            }
            self._conn.execute(
                f"INSERT INTO agents ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    agent["id"], env_id, name, framework, agent["status"],
                    json.dumps(metadata), agent["created_at_ms"],
                ),
            )
            self._conn.commit()
            return agent, True

    def get(self, agent_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLS} FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
        return _row(row) if row else None

    def list(
        self, env_id: Optional[str], limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        where = "WHERE env_id = ?" if env_id else ""
        params: tuple = (env_id,) if env_id else ()
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM agents {where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_COLS} FROM agents {where} ORDER BY created_at_ms, id "
                "LIMIT ? OFFSET ?",
                params + (limit, offset),
            ).fetchall()
        return [_row(r) for r in rows], total

    def update(
        self,
        agent_id: str,
        name: Optional[str],
        status: Optional[str],
        metadata: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """Apply the provided fields; returns the updated agent or None."""
        sets: list[str] = []
        params: list[Any] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if metadata is not None:
            sets.append("metadata = ?")
            params.append(json.dumps(metadata))
        with self._lock:
            if sets:
                cur = self._conn.execute(
                    f"UPDATE agents SET {', '.join(sets)} WHERE id = ?",
                    (*params, agent_id),
                )
                self._conn.commit()
                if cur.rowcount == 0:
                    return None
            row = self._conn.execute(
                f"SELECT {_COLS} FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
        return _row(row) if row else None
