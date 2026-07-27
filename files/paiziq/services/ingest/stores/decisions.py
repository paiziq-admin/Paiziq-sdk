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
_REVIEW_FIELDS = (
    "id",
    "payment_id",
    "decision_id",
    "state",
    "reviewer_id",
    "note",
    "created_at_ms",
    "resolved_at_ms",
    "sla_deadline_ms",
    "priority",
    "last_action",
    "assigned_at_ms",
    "updated_at_ms",
)
_REVIEW_COLS = ", ".join(_REVIEW_FIELDS)
_REVIEW_JOIN_COLS = ", ".join(f"r.{field}" for field in _REVIEW_FIELDS)


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
        "priority": row[9], "last_action": row[10], "assigned_at_ms": row[11],
        "updated_at_ms": row[12],
    }


class ReviewNotFound(Exception):
    """Raised when a workflow mutation targets an unknown review."""


class ReviewStateConflict(Exception):
    """Raised when an action requires an open review."""

    def __init__(self, state: str) -> None:
        super().__init__(f"review is {state}")
        self.state = state


class ReviewAssignmentConflict(Exception):
    """Raised when a different reviewer owns the queue item."""

    def __init__(self, reviewer_id: Optional[str]) -> None:
        super().__init__(f"review is assigned to {reviewer_id or 'nobody'}")
        self.reviewer_id = reviewer_id


class ReviewPaymentConflict(Exception):
    """Raised when the payment can no longer be resolved by review."""

    def __init__(self, state: str) -> None:
        super().__init__(f"payment is {state}")
        self.state = state


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
            existing = self._conn.execute(
                "SELECT id FROM reviews WHERE payment_id = ? AND state = 'open' "
                "ORDER BY created_at_ms, id LIMIT 1",
                (payment_id,),
            ).fetchone()
            if existing is not None:
                review_id = existing[0]
                self._conn.execute(
                    "UPDATE reviews SET decision_id = ?, updated_at_ms = ? WHERE id = ?",
                    (decision_id, created, review_id),
                )
            else:
                self._conn.execute(
                    "INSERT INTO reviews "
                    "(id, payment_id, decision_id, state, created_at_ms, sla_deadline_ms, "
                    "updated_at_ms) VALUES (?, ?, ?, 'open', ?, ?, ?)",
                    (review_id, payment_id, decision_id, created, sla_deadline, created),
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

    def open_for_payment(self, payment_id: str) -> Optional[dict[str, Any]]:
        """Return the canonical open review for a payment, if one exists."""

        with self._lock:
            row = self._conn.execute(
                f"SELECT {_REVIEW_COLS} FROM reviews "
                "WHERE payment_id = ? AND state = 'open' "
                "ORDER BY created_at_ms, id LIMIT 1",
                (payment_id,),
            ).fetchone()
        return _review_row(row) if row else None

    def list(
        self,
        state: Optional[str],
        env_id: Optional[str],
        reviewer_id: Optional[str],
        priority: Optional[str],
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("r.state", state),
            ("p.env_id", env_id),
            ("r.reviewer_id", reviewer_id),
            ("r.priority", priority),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM reviews r "
                f"JOIN payments p ON p.id = r.payment_id {where}",
                params,
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_REVIEW_JOIN_COLS} FROM reviews r "
                f"JOIN payments p ON p.id = r.payment_id {where} "
                "ORDER BY "
                "CASE r.state WHEN 'open' THEN 0 ELSE 1 END, "
                "CASE r.priority "
                "WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'normal' THEN 2 ELSE 3 END, "
                "r.sla_deadline_ms IS NULL, r.sla_deadline_ms, "
                "r.created_at_ms DESC, r.id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [_review_row(row) for row in rows], total

    def _workflow_row(self, review_id: str) -> tuple:
        row = self._conn.execute(
            "SELECT state, reviewer_id, payment_id FROM reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        if row is None:
            raise ReviewNotFound(review_id)
        if row[0] != "open":
            raise ReviewStateConflict(row[0])
        return row

    def claim(self, review_id: str, reviewer_id: str) -> dict[str, Any]:
        ts = now_ms()
        with self._lock:
            row = self._workflow_row(review_id)
            current = row[1]
            if current is not None and current != reviewer_id:
                raise ReviewAssignmentConflict(current)
            self._conn.execute(
                "UPDATE reviews SET reviewer_id = ?, last_action = 'claimed', "
                "assigned_at_ms = COALESCE(assigned_at_ms, ?), updated_at_ms = ? "
                "WHERE id = ?",
                (reviewer_id, ts, ts, review_id),
            )
            self._conn.commit()
        record = self.get(review_id)
        assert record is not None
        return record

    def release(self, review_id: str, reviewer_id: str, note: Optional[str]) -> dict[str, Any]:
        ts = now_ms()
        with self._lock:
            row = self._workflow_row(review_id)
            if row[1] != reviewer_id:
                raise ReviewAssignmentConflict(row[1])
            self._conn.execute(
                "UPDATE reviews SET reviewer_id = NULL, note = COALESCE(?, note), "
                "last_action = 'released', assigned_at_ms = NULL, updated_at_ms = ? "
                "WHERE id = ?",
                (note, ts, review_id),
            )
            self._conn.commit()
        record = self.get(review_id)
        assert record is not None
        return record

    def reassign(
        self,
        review_id: str,
        actor_reviewer_id: str,
        reviewer_id: str,
        note: str,
        *,
        allow_override: bool = False,
    ) -> dict[str, Any]:
        ts = now_ms()
        with self._lock:
            row = self._workflow_row(review_id)
            if not allow_override and row[1] != actor_reviewer_id:
                raise ReviewAssignmentConflict(row[1])
            self._conn.execute(
                "UPDATE reviews SET reviewer_id = ?, note = ?, "
                "last_action = 'reassigned', assigned_at_ms = ?, updated_at_ms = ? "
                "WHERE id = ?",
                (reviewer_id, note, ts, ts, review_id),
            )
            self._conn.commit()
        record = self.get(review_id)
        assert record is not None
        return record

    def annotate(
        self,
        review_id: str,
        reviewer_id: str,
        note: str,
        action: str,
        priority: Optional[str] = None,
    ) -> dict[str, Any]:
        if action not in {"requested_info", "escalated"}:
            raise ValueError(f"unsupported review annotation: {action}")
        ts = now_ms()
        with self._lock:
            row = self._workflow_row(review_id)
            current = row[1]
            if current is not None and current != reviewer_id:
                raise ReviewAssignmentConflict(current)
            self._conn.execute(
                "UPDATE reviews SET reviewer_id = ?, note = ?, last_action = ?, "
                "priority = COALESCE(?, priority), "
                "assigned_at_ms = COALESCE(assigned_at_ms, ?), updated_at_ms = ? "
                "WHERE id = ?",
                (reviewer_id, note, action, priority, ts, ts, review_id),
            )
            self._conn.commit()
        record = self.get(review_id)
        assert record is not None
        return record

    def resolve(
        self,
        review_id: str,
        reviewer_id: str,
        note: str,
        outcome: str,
    ) -> dict[str, Any]:
        """Resolve the review and its payment in one SQLite transaction."""
        if outcome not in {"approved", "rejected"}:
            raise ValueError(f"unsupported review outcome: {outcome}")
        ts = now_ms()
        with self._lock:
            try:
                row = self._workflow_row(review_id)
                current, payment_id = row[1], row[2]
                if current is not None and current != reviewer_id:
                    raise ReviewAssignmentConflict(current)
                payment = self._conn.execute(
                    "SELECT state FROM payments WHERE id = ?", (payment_id,)
                ).fetchone()
                if payment is None:
                    raise ReviewPaymentConflict("missing")
                if payment[0] != "needs_review":
                    raise ReviewPaymentConflict(payment[0])
                self._conn.execute(
                    "UPDATE payments SET state = ?, updated_at_ms = ? WHERE id = ?",
                    (outcome, ts, payment_id),
                )
                self._conn.execute(
                    "INSERT INTO payment_transitions "
                    "(payment_id, from_state, to_state, actor, reason, at_ms) "
                    "VALUES (?, 'needs_review', ?, ?, ?, ?)",
                    (payment_id, outcome, f"reviewer:{reviewer_id}", note, ts),
                )
                self._conn.execute(
                    "UPDATE reviews SET state = ?, reviewer_id = ?, note = ?, "
                    "last_action = ?, assigned_at_ms = COALESCE(assigned_at_ms, ?), "
                    "resolved_at_ms = ?, updated_at_ms = ? WHERE id = ?",
                    (outcome, reviewer_id, note, outcome, ts, ts, ts, review_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        record = self.get(review_id)
        assert record is not None
        return record
