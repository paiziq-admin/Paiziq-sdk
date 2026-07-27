"""Webhook endpoint and delivery persistence (PZ-076/PZ-077)."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from ids import new_id, now_ms
from field_secrets import decrypt_secret, encrypt_secret, generate_webhook_secret

_ENDPOINT_COLS = "id, env_id, url, secret, events, status, created_at_ms"
_DELIVERY_COLS = (
    "id, endpoint_id, event_type, payload, state, attempts, "
    "next_attempt_ms, last_error, created_at_ms, updated_at_ms"
)
_DELIVERY_JOIN_COLS = ", ".join(
    f"wd.{column.strip()}" for column in _DELIVERY_COLS.split(",")
)

RETRY_MS = [60_000, 300_000, 1_800_000, 7_200_000]
MAX_ATTEMPTS = 5

def _endpoint_row(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0], "env_id": row[1], "url": row[2], "secret": row[3],
        "events": json.loads(row[4]), "status": row[5], "created_at_ms": row[6],
    }


def _delivery_row(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0], "endpoint_id": row[1], "event_type": row[2],
        "payload": json.loads(row[3]), "state": row[4], "attempts": row[5],
        "next_attempt_ms": row[6], "last_error": row[7],
        "created_at_ms": row[8], "updated_at_ms": row[9],
    }


class WebhookStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.Lock,
        secrets_key: Optional[str] = None,
    ) -> None:
        self._conn = conn
        self._lock = lock
        self._secrets_key = secrets_key

    def _public_endpoint(self, row: tuple) -> dict[str, Any]:
        record = _endpoint_row(row)
        record.pop("secret", None)
        return record

    def create_endpoint(
        self, env_id: str, url: str, events: list[str]
    ) -> tuple[dict[str, Any], str]:
        secret = generate_webhook_secret()
        stored = encrypt_secret(secret, self._secrets_key)
        endpoint_id = new_id("whe")
        created = now_ms()
        with self._lock:
            self._conn.execute(
                "INSERT INTO webhook_endpoints "
                "(id, env_id, url, secret, events, status, created_at_ms) "
                "VALUES (?, ?, ?, ?, ?, 'active', ?)",
                (endpoint_id, env_id, url, stored, json.dumps(events), created),
            )
            self._conn.commit()
        record = self.get_endpoint(endpoint_id)
        assert record is not None
        return record, secret

    def get_endpoint(self, endpoint_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_ENDPOINT_COLS} FROM webhook_endpoints WHERE id = ?",
                (endpoint_id,),
            ).fetchone()
        return self._public_endpoint(row) if row else None

    def get_endpoint_secret(self, endpoint_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT secret FROM webhook_endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
        if row is None:
            return None
        return decrypt_secret(row[0], self._secrets_key)

    def list_endpoints(
        self, env_id: Optional[str], limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        where = "WHERE env_id = ?" if env_id else ""
        params: tuple = (env_id,) if env_id else ()
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM webhook_endpoints {where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_ENDPOINT_COLS} FROM webhook_endpoints {where} "
                "ORDER BY created_at_ms, id LIMIT ? OFFSET ?",
                params + (limit, offset),
            ).fetchall()
        return [self._public_endpoint(r) for r in rows], total

    def update_endpoint(
        self,
        endpoint_id: str,
        *,
        url: Optional[str] = None,
        events: Optional[list[str]] = None,
        status: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        updates: list[str] = []
        params: list[Any] = []
        if url is not None:
            updates.append("url = ?")
            params.append(url)
        if events is not None:
            updates.append("events = ?")
            params.append(json.dumps(events))
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if not updates:
            return self.get_endpoint(endpoint_id)
        params.append(endpoint_id)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE webhook_endpoints SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            self._conn.commit()
        return self.get_endpoint(endpoint_id) if cur.rowcount else None

    def active_for_event(self, env_id: str, event_type: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_ENDPOINT_COLS} FROM webhook_endpoints "
                "WHERE env_id = ? AND status = 'active'",
                (env_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            events = json.loads(row[4])
            if "*" in events or event_type in events:
                out.append(_endpoint_row(row))
        return out

    def enqueue(
        self, endpoint_id: str, event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        delivery_id = new_id("whd")
        ts = now_ms()
        with self._lock:
            self._conn.execute(
                "INSERT INTO webhook_deliveries "
                "(id, endpoint_id, event_type, payload, state, attempts, "
                "next_attempt_ms, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
                (delivery_id, endpoint_id, event_type, json.dumps(payload), ts, ts, ts),
            )
            self._conn.commit()
        record = self.get_delivery(delivery_id)
        assert record is not None
        return record


    def get_delivery(self, delivery_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_DELIVERY_COLS} FROM webhook_deliveries WHERE id = ?",
                (delivery_id,),
            ).fetchone()
        return _delivery_row(row) if row else None

    def list_deliveries(
        self,
        endpoint_id: Optional[str],
        state: Optional[str],
        limit: int,
        offset: int,
        *,
        env_id: Optional[str] = None,
        event_type: Optional[str] = None,
        payment_id: Optional[str] = None,
        review_id: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if endpoint_id:
            clauses.append("wd.endpoint_id = ?")
            params.append(endpoint_id)
        if state:
            clauses.append("wd.state = ?")
            params.append(state)
        if env_id:
            clauses.append("we.env_id = ?")
            params.append(env_id)
        if event_type:
            clauses.append("wd.event_type = ?")
            params.append(event_type)
        if payment_id:
            clauses.append("json_extract(wd.payload, '$.data.payment_id') = ?")
            params.append(payment_id)
        if review_id:
            clauses.append("json_extract(wd.payload, '$.data.review_id') = ?")
            params.append(review_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM webhook_deliveries wd "
                f"JOIN webhook_endpoints we ON we.id = wd.endpoint_id {where}",
                tuple(params),
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_DELIVERY_JOIN_COLS} FROM webhook_deliveries wd "
                f"JOIN webhook_endpoints we ON we.id = wd.endpoint_id {where} "
                "ORDER BY wd.created_at_ms DESC, wd.id LIMIT ? OFFSET ?",
                tuple(params) + (limit, offset),
            ).fetchall()
        return [_delivery_row(r) for r in rows], total

    def claim_due(self, limit: int = 20) -> list[dict[str, Any]]:
        ts = now_ms()
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_DELIVERY_COLS} FROM webhook_deliveries "
                "WHERE state = 'pending' AND next_attempt_ms <= ? "
                "ORDER BY next_attempt_ms LIMIT ?",
                (ts, limit),
            ).fetchall()
        return [_delivery_row(r) for r in rows]

    def mark_delivered(self, delivery_id: str) -> None:
        ts = now_ms()
        with self._lock:
            self._conn.execute(
                "UPDATE webhook_deliveries SET state = 'delivered', updated_at_ms = ? "
                "WHERE id = ?",
                (ts, delivery_id),
            )
            self._conn.commit()

    def mark_failed(
        self, delivery_id: str, attempts: int, error: str, status_code: Optional[int]
    ) -> str:
        new_attempts = attempts + 1
        ts = now_ms()
        if new_attempts >= MAX_ATTEMPTS:
            state = "dead"
            next_ms = ts
        else:
            state = "pending"
            idx = min(new_attempts - 1, len(RETRY_MS) - 1)
            next_ms = ts + RETRY_MS[idx]
        with self._lock:
            self._conn.execute(
                "UPDATE webhook_deliveries SET state = ?, attempts = ?, "
                "next_attempt_ms = ?, last_error = ?, updated_at_ms = ? WHERE id = ?",
                (state, new_attempts, next_ms, error[:500], ts, delivery_id),
            )
            self._conn.commit()
        return state

    def append_log(
        self,
        delivery_id: str,
        attempt: int,
        status_code: Optional[int],
        error: Optional[str],
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO webhook_delivery_logs "
                "(delivery_id, attempt, status_code, error, at_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (delivery_id, attempt, status_code, error, now_ms()),
            )
            self._conn.commit()

    def delivery_logs(self, delivery_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT attempt, status_code, error, at_ms "
                "FROM webhook_delivery_logs WHERE delivery_id = ? ORDER BY attempt",
                (delivery_id,),
            ).fetchall()
        return [
            {"attempt": r[0], "status_code": r[1], "error": r[2], "at_ms": r[3]}
            for r in rows
        ]
