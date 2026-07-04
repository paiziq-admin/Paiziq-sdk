"""Data retention purge jobs (PZ-081)."""

from __future__ import annotations

import sqlite3
import threading
from typing import Optional

from ids import now_ms


class RetentionJob:
    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.Lock,
        spans_days: Optional[int],
        notifications_days: Optional[int],
        audit_days: Optional[int],
        audit_min_days: int = 365,
    ) -> None:
        self._conn = conn
        self._lock = lock
        self._spans_days = spans_days
        self._notifications_days = notifications_days
        self._audit_days = audit_days
        self._audit_min_days = audit_min_days

    def run(self) -> dict[str, int]:
        cutoff = now_ms()
        deleted: dict[str, int] = {"spans": 0, "notifications": 0, "audit_log": 0}
        with self._lock:
            if self._spans_days:
                ms = cutoff - self._spans_days * 86_400_000
                cur = self._conn.execute(
                    "DELETE FROM span_events WHERE at_ms IS NOT NULL AND at_ms < ?", (ms,)
                )
                deleted["span_events"] = cur.rowcount
                cur = self._conn.execute(
                    "DELETE FROM spans WHERE start_ms IS NOT NULL AND start_ms < ?", (ms,)
                )
                deleted["spans"] = cur.rowcount
            if self._notifications_days:
                ms = cutoff - self._notifications_days * 86_400_000
                cur = self._conn.execute(
                    "DELETE FROM notifications WHERE created_at_ms IS NOT NULL AND created_at_ms < ?",
                    (ms,),
                )
                deleted["notifications"] = cur.rowcount
            if self._audit_days and self._audit_days >= self._audit_min_days:
                ms = cutoff - self._audit_days * 86_400_000
                cur = self._conn.execute("DELETE FROM audit_log WHERE at_ms < ?", (ms,))
                deleted["audit_log"] = cur.rowcount
            self._conn.commit()
        return deleted
