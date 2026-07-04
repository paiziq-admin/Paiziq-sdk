"""Policy persistence with immutable published versions (contract §10).

Drafts live on the policy row and are mutable; published versions are
append-only rows in `policy_versions` with monotonically increasing
numbers. At most one version is active per *environment*: publishing
deactivates the previous active version, so decisions always resolve a
single policy. Version rows are never updated or deleted — rollback
publishes a new version copying an older document.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from ids import new_id, now_ms

_POLICY_COLS = "id, env_id, name, draft_document, created_at_ms"
_VERSION_COLS = "policy_id, version, document, is_active, published_at_ms"


def _version_row(row: tuple) -> dict[str, Any]:
    return {
        "policy_id": row[0], "version": row[1], "document": json.loads(row[2]),
        "is_active": bool(row[3]), "published_at_ms": row[4],
    }


class PolicyStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def _policy_dict(self, row: tuple) -> dict[str, Any]:
        """Assemble a policy record; caller must hold the lock."""
        active = self._conn.execute(
            "SELECT version FROM policy_versions WHERE policy_id = ? AND is_active = 1",
            (row[0],),
        ).fetchone()
        latest = self._conn.execute(
            "SELECT MAX(version) FROM policy_versions WHERE policy_id = ?", (row[0],)
        ).fetchone()
        return {
            "id": row[0], "env_id": row[1], "name": row[2],
            "draft_document": json.loads(row[3]) if row[3] else None,
            "active_version": active[0] if active else None,
            "latest_version": latest[0],
            "created_at_ms": row[4],
        }

    def create(self, env_id: str, name: str, draft_document: dict[str, Any]) -> dict[str, Any]:
        policy_id = new_id("pol")
        with self._lock:
            self._conn.execute(
                f"INSERT INTO policies ({_POLICY_COLS}) VALUES (?, ?, ?, ?, ?)",
                (policy_id, env_id, name, json.dumps(draft_document), now_ms()),
            )
            self._conn.commit()
        record = self.get(policy_id)
        assert record is not None
        return record

    def get(self, policy_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_POLICY_COLS} FROM policies WHERE id = ?", (policy_id,)
            ).fetchone()
            return self._policy_dict(row) if row else None

    def list(
        self, env_id: Optional[str], limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        where = "WHERE env_id = ?" if env_id else ""
        params: tuple = (env_id,) if env_id else ()
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM policies {where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_POLICY_COLS} FROM policies {where} ORDER BY created_at_ms, id "
                "LIMIT ? OFFSET ?",
                params + (limit, offset),
            ).fetchall()
            return [self._policy_dict(r) for r in rows], total

    def publish(self, policy_id: str, document: dict[str, Any]) -> dict[str, Any]:
        """Publish `document` as the next immutable version and make it the
        environment's single active version."""
        with self._lock:
            env_row = self._conn.execute(
                "SELECT env_id FROM policies WHERE id = ?", (policy_id,)
            ).fetchone()
            assert env_row is not None  # router checks existence first
            nxt = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM policy_versions "
                "WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()[0]
            self._conn.execute(
                "UPDATE policy_versions SET is_active = 0 WHERE is_active = 1 AND "
                "policy_id IN (SELECT id FROM policies WHERE env_id = ?)",
                (env_row[0],),
            )
            self._conn.execute(
                f"INSERT INTO policy_versions ({_VERSION_COLS}) VALUES (?, ?, ?, 1, ?)",
                (policy_id, nxt, json.dumps(document), now_ms()),
            )
            self._conn.commit()
        version = self.get_version(policy_id, nxt)
        assert version is not None
        return version

    def get_version(self, policy_id: str, version: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_VERSION_COLS} FROM policy_versions "
                "WHERE policy_id = ? AND version = ?",
                (policy_id, version),
            ).fetchone()
        return _version_row(row) if row else None

    def versions(self, policy_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_VERSION_COLS} FROM policy_versions WHERE policy_id = ? "
                "ORDER BY version",
                (policy_id,),
            ).fetchall()
        return [_version_row(r) for r in rows]

    def update_draft(self, policy_id: str, document: dict[str, Any]) -> Optional[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE policies SET draft_document = ? WHERE id = ?",
                (json.dumps(document), policy_id),
            )
            self._conn.commit()
        return self.get(policy_id) if cur.rowcount else None

    def active_for_env(self, env_id: str) -> Optional[dict[str, Any]]:
        """The environment's single active published version, if any."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_VERSION_COLS} FROM policy_versions "
                "WHERE is_active = 1 AND policy_id IN "
                "(SELECT id FROM policies WHERE env_id = ?)",
                (env_id,),
            ).fetchone()
        return _version_row(row) if row else None
