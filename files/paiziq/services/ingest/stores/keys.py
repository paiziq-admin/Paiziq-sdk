"""API key persistence and verification (contract §6).

Secrets are generated server-side (`pzq_<env-kind>_<random hex>`),
stored only as SHA-256 hashes, and returned in plaintext exactly once
from create/rotate. Rotation may keep the previous secret valid until
grace_until_ms; revocation is a soft delete (row kept for audit).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from typing import Any, Optional

from ids import new_id, now_ms

PREFIX_LEN = 12
_COLS = (
    "id, env_id, name, scope, secret_prefix, created_at_ms, "
    "rotated_at_ms, revoked_at_ms, grace_until_ms"
)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _row(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0], "env_id": row[1], "name": row[2], "scope": row[3],
        "secret_prefix": row[4], "created_at_ms": row[5],
        "rotated_at_ms": row[6], "revoked_at_ms": row[7], "grace_until_ms": row[8],
    }


def _new_secret(env_kind: str) -> str:
    return f"pzq_{env_kind}_{secrets.token_hex(16)}"


class KeyStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def create(
        self, env_id: str, env_kind: str, name: str, scope: str
    ) -> tuple[dict[str, Any], str]:
        """Create a key; returns (record, plaintext secret — shown once)."""
        secret = _new_secret(env_kind)
        key_id = new_id("key")
        created = now_ms()
        with self._lock:
            self._conn.execute(
                "INSERT INTO api_keys (id, env_id, name, scope, secret_hash, secret_prefix, "
                "created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key_id, env_id, name, scope, _hash(secret), secret[:PREFIX_LEN], created),
            )
            self._conn.commit()
        record = self.get(key_id)
        assert record is not None
        return record, secret

    def get(self, key_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLS} FROM api_keys WHERE id = ?", (key_id,)
            ).fetchone()
        return _row(row) if row else None

    def list(
        self, env_id: Optional[str], limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        where = "WHERE env_id = ?" if env_id else ""
        params: tuple = (env_id,) if env_id else ()
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM api_keys {where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_COLS} FROM api_keys {where} ORDER BY created_at_ms, id "
                "LIMIT ? OFFSET ?",
                params + (limit, offset),
            ).fetchall()
        return [_row(r) for r in rows], total

    def rotate(
        self, key_id: str, env_kind: str, grace_seconds: int
    ) -> Optional[tuple[dict[str, Any], str]]:
        """Issue a new secret; old secret stays valid for grace_seconds."""
        secret = _new_secret(env_kind)
        ts = now_ms()
        grace_until = ts + grace_seconds * 1000 if grace_seconds > 0 else None
        with self._lock:
            cur = self._conn.execute(
                "UPDATE api_keys SET previous_secret_hash = CASE WHEN ? IS NULL THEN NULL "
                "ELSE secret_hash END, secret_hash = ?, secret_prefix = ?, rotated_at_ms = ?, "
                "grace_until_ms = ? WHERE id = ? AND revoked_at_ms IS NULL",
                (grace_until, _hash(secret), secret[:PREFIX_LEN], ts, grace_until, key_id),
            )
            self._conn.commit()
        if cur.rowcount == 0:
            return None
        record = self.get(key_id)
        assert record is not None
        return record, secret

    def revoke(self, key_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE api_keys SET revoked_at_ms = ? WHERE id = ? AND revoked_at_ms IS NULL",
                (now_ms(), key_id),
            )
            self._conn.commit()
        return self.get(key_id) if cur.rowcount else None

    def verify(self, secret: str) -> Optional[dict[str, Any]]:
        """Resolve a presented secret to an active key record, else None."""
        digest = _hash(secret)
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLS} FROM api_keys WHERE revoked_at_ms IS NULL AND "
                "(secret_hash = ? OR (previous_secret_hash = ? AND grace_until_ms >= ?))",
                (digest, digest, now_ms()),
            ).fetchone()
        return _row(row) if row else None
