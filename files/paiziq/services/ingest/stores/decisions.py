"""Decision and review persistence (contract §8/§9).

Decisions are immutable snapshots of an engine evaluation;
re-evaluating a payment appends a new record. Reviews open when a
decision lands on `needs_review`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from ids import new_id, now_ms

_DECISION_COLS = "id, payment_id, policy_version, verdict, reasons, risk_flags, created_at_ms"
_REVIEW_COLS = (
    "id, payment_id, decision_id, state, reviewer_id, note, created_at_ms, resolved_at_ms, sla_deadline_ms"
)


def _decision_row(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0], "payment_id": row[1], "policy_version": row[2], "verdict": row[3],
        "reasons": json.loads(row[4]), "risk_flags": json.loads(row[5]),
        "created_at_ms": row[6],
    }


def _review_row(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0], "payment_id": row[1], "decision_id": row[2], "state": row[3],
        "reviewer_id": row[4], "note": row[5], "created_at_ms": row[6],
        "resolved_at_ms": row[7], "sla_deadline_ms": row[8],
    }


class DecisionStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def create(
        self,
        payment_id: str,
        policy_version: Optional[int],
        verdict: str,
        reasons: list[str],
        risk_flags: list[str],
    ) -> dict[str, Any]:
        decision_id = new_id("dec")
        with self._lock:
            self._conn.execute(
                f"INSERT INTO decisions ({_DECISION_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    decision_id, payment_id, policy_version, verdict,
                    json.dumps(reasons), json.dumps(risk_flags), now_ms(),
                ),
            )
            self._conn.commit()
        record = self.get(decision_id)
        assert record is not None
        return record

    def get(self, decision_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_DECISION_COLS} FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        return _decision_row(row) if row else None

    def list(
        self, payment_id: Optional[str], limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        where = "WHERE payment_id = ?" if payment_id else ""
        params: tuple = (payment_id,) if payment_id else ()
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM decisions {where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_DECISION_COLS} FROM decisions {where} "
                "ORDER BY created_at_ms, id LIMIT ? OFFSET ?",
                params + (limit, offset),
            ).fetchall()
        return [_decision_row(r) for r in rows], total


class ReviewStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def open(self, payment_id: str, decision_id: str, sla_ms: int = 0) -> dict[str, Any]:
        review_id = new_id("rev")
        created = now_ms()
        sla_deadline = created + sla_ms if sla_ms > 0 else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO reviews (id, payment_id, decision_id, state, created_at_ms, sla_deadline_ms) "
                "VALUES (?, ?, ?, 'open', ?, ?)",
                (review_id, payment_id, decision_id, created, sla_deadline),
            )
            self._conn.commit()
        record = self.get(review_id)
        assert record is not None
        return record

    def get(self, review_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_REVIEW_COLS} FROM reviews WHERE id = ?", (review_id,)
            ).fetchone()
        return _review_row(row) if row else None

    def for_payment(self, payment_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_REVIEW_COLS} FROM reviews WHERE payment_id = ? ORDER BY id",
                (payment_id,),
            ).fetchall()
        return [_review_row(r) for r in rows]
