"""Organization and environment persistence (contract §4).

Uniqueness (org name globally, environment name per org) is enforced
by the migration-0002 schema; IntegrityError surfaces as 409 conflict
in the router layer.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Optional

from ids import new_id, now_ms

_ORG_COLS = "id, name, created_at_ms"
_ENV_COLS = "id, org_id, name, kind, created_at_ms"


def _org_row(row: tuple) -> dict[str, Any]:
    return {"id": row[0], "name": row[1], "created_at_ms": row[2]}


def _env_row(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0], "org_id": row[1], "name": row[2],
        "kind": row[3], "created_at_ms": row[4],
    }


class OrgStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def create_org(self, name: str) -> dict[str, Any]:
        org = {"id": new_id("org"), "name": name, "created_at_ms": now_ms()}
        with self._lock:
            self._conn.execute(
                f"INSERT INTO organizations ({_ORG_COLS}) VALUES (?, ?, ?)",
                (org["id"], org["name"], org["created_at_ms"]),
            )
            self._conn.commit()
        return org

    def get_org(self, org_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_ORG_COLS} FROM organizations WHERE id = ?", (org_id,)
            ).fetchone()
        return _org_row(row) if row else None

    def list_orgs(self, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_ORG_COLS} FROM organizations ORDER BY created_at_ms, id "
                "LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_org_row(r) for r in rows], total

    def create_environment(self, org_id: str, name: str, kind: str) -> dict[str, Any]:
        env = {
            "id": new_id("env"), "org_id": org_id, "name": name,
            "kind": kind, "created_at_ms": now_ms(),
        }
        with self._lock:
            self._conn.execute(
                f"INSERT INTO environments ({_ENV_COLS}) VALUES (?, ?, ?, ?, ?)",
                (env["id"], env["org_id"], env["name"], env["kind"], env["created_at_ms"]),
            )
            self._conn.commit()
        return env

    def get_environment(self, env_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_ENV_COLS} FROM environments WHERE id = ?", (env_id,)
            ).fetchone()
        return _env_row(row) if row else None

    def list_environments(
        self, org_id: str, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM environments WHERE org_id = ?", (org_id,)
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_ENV_COLS} FROM environments WHERE org_id = ? "
                "ORDER BY created_at_ms, id LIMIT ? OFFSET ?",
                (org_id, limit, offset),
            ).fetchall()
        return [_env_row(r) for r in rows], total
