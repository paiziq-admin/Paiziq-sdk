"""Payment proposal persistence and state transitions (contract §7).

The state machine is server-enforced here; transition history is
append-only (`payment_transitions`) and every transition bumps the
payment's `updated_at_ms`.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Optional

from ids import new_id, now_ms

_COLS = (
    "id, env_id, agent_id, principal_id, merchant, amount, currency, "
    "intent_description, state, request_id, created_at_ms, updated_at_ms"
)

# state → states it may transition to (contract §7)
TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"approved", "needs_review", "rejected"}),
    "approved": frozenset({"executed", "failed"}),
    "needs_review": frozenset({"approved", "rejected"}),
    "rejected": frozenset(),
    "executed": frozenset(),
    "failed": frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(f"cannot transition {from_state} -> {to_state}")
        self.from_state = from_state
        self.to_state = to_state


def _row(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0], "env_id": row[1], "agent_id": row[2], "principal_id": row[3],
        "merchant": row[4], "amount": row[5], "currency": row[6],
        "intent_description": row[7], "state": row[8], "request_id": row[9],
        "created_at_ms": row[10], "updated_at_ms": row[11],
    }


class PaymentStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def create(
        self,
        env_id: str,
        agent_id: str,
        principal_id: str,
        merchant: str,
        amount: float,
        currency: str,
        intent_description: str,
        request_id: Optional[str],
        idempotency_key: Optional[str],
    ) -> dict[str, Any]:
        ts = now_ms()
        payment_id = new_id("pay")
        with self._lock:
            self._conn.execute(
                f"INSERT INTO payments ({_COLS}, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payment_id, env_id, agent_id, principal_id, merchant, amount,
                    currency, intent_description, "proposed", request_id, ts, ts,
                    idempotency_key,
                ),
            )
            self._conn.commit()
        record = self.get(payment_id)
        assert record is not None
        return record

    def get(self, payment_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLS} FROM payments WHERE id = ?", (payment_id,)
            ).fetchone()
        return _row(row) if row else None

    def find_by_idempotency_key(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLS} FROM payments WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return _row(row) if row else None

    def list(
        self,
        env_id: Optional[str],
        agent_id: Optional[str],
        state: Optional[str],
        limit: int,
        offset: int,
        *,
        currency: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        query: Optional[str] = None,
        from_ms: Optional[int] = None,
        to_ms: Optional[int] = None,
        sort: str = "created_desc",
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("env_id", env_id), ("agent_id", agent_id), ("state", state)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if currency is not None:
            clauses.append("currency = ?")
            params.append(currency.upper())
        if min_amount is not None:
            clauses.append("amount >= ?")
            params.append(min_amount)
        if max_amount is not None:
            clauses.append("amount <= ?")
            params.append(max_amount)
        if from_ms is not None:
            clauses.append("created_at_ms >= ?")
            params.append(from_ms)
        if to_ms is not None:
            clauses.append("created_at_ms <= ?")
            params.append(to_ms)
        if query:
            escaped = (
                query.lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            clauses.append(
                "("
                "LOWER(id) LIKE ? ESCAPE '\\' OR "
                "LOWER(agent_id) LIKE ? ESCAPE '\\' OR "
                "LOWER(principal_id) LIKE ? ESCAPE '\\' OR "
                "LOWER(merchant) LIKE ? ESCAPE '\\' OR "
                "LOWER(COALESCE(request_id, '')) LIKE ? ESCAPE '\\' OR "
                "LOWER(intent_description) LIKE ? ESCAPE '\\'"
                ")"
            )
            params.extend([f"%{escaped}%"] * 6)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order_by = {
            "created_desc": "created_at_ms DESC, id DESC",
            "created_asc": "created_at_ms, id",
            "amount_desc": "amount DESC, created_at_ms DESC, id DESC",
            "amount_asc": "amount, created_at_ms DESC, id DESC",
            "merchant_asc": "LOWER(merchant), created_at_ms DESC, id DESC",
        }.get(sort, "created_at_ms DESC, id DESC")
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM payments {where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_COLS} FROM payments {where} ORDER BY {order_by} "
                "LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [_row(r) for r in rows], total

    def transition(
        self, payment_id: str, to_state: str, actor: str, reason: Optional[str]
    ) -> dict[str, Any]:
        """Apply a state transition; raises InvalidTransition when barred.

        Caller must ensure the payment exists (KeyError otherwise).
        """
        ts = now_ms()
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM payments WHERE id = ?", (payment_id,)
            ).fetchone()
            if row is None:
                raise KeyError(payment_id)
            from_state = row[0]
            if to_state not in TRANSITIONS[from_state]:
                raise InvalidTransition(from_state, to_state)
            self._conn.execute(
                "UPDATE payments SET state = ?, updated_at_ms = ? WHERE id = ?",
                (to_state, ts, payment_id),
            )
            self._conn.execute(
                "INSERT INTO payment_transitions "
                "(payment_id, from_state, to_state, actor, reason, at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (payment_id, from_state, to_state, actor, reason, ts),
            )
            self._conn.commit()
        record = self.get(payment_id)
        assert record is not None
        return record

    def transitions_for(self, payment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT from_state, to_state, actor, reason, at_ms "
                "FROM payment_transitions WHERE payment_id = ? ORDER BY id",
                (payment_id,),
            ).fetchall()
        return [
            {"from": r[0], "to": r[1], "actor": r[2], "reason": r[3], "at_ms": r[4]}
            for r in rows
        ]
