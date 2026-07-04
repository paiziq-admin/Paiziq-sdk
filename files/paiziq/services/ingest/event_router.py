"""Internal event fan-out to webhook subscribers (PZ-078)."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from ids import now_ms
from stores.webhooks import WebhookStore


class EventRouter:
    def __init__(
        self,
        webhooks: WebhookStore,
        conn: sqlite3.Connection,
        lock: threading.Lock,
        review_sla_ms: int,
    ) -> None:
        self._webhooks = webhooks
        self._conn = conn
        self._lock = lock
        self._review_sla_ms = review_sla_ms

    def dispatch(self, env_id: str, event_type: str, payload: dict[str, Any]) -> int:
        endpoints = self._webhooks.active_for_event(env_id, event_type)
        envelope = {"type": event_type, "created_at_ms": now_ms(), "data": payload}
        for ep in endpoints:
            self._webhooks.enqueue(ep["id"], event_type, envelope)
        return len(endpoints)

    def check_sla_breaches(self) -> int:
        ts = now_ms()
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.id, r.payment_id, p.env_id, r.sla_deadline_ms "
                "FROM reviews r JOIN payments p ON p.id = r.payment_id "
                "WHERE r.state = 'open' AND r.sla_deadline_ms IS NOT NULL "
                "AND r.sla_deadline_ms <= ?",
                (ts,),
            ).fetchall()
        count = 0
        for review_id, payment_id, env_id, deadline in rows:
            payload = {
                "review_id": review_id,
                "payment_id": payment_id,
                "sla_deadline_ms": deadline,
            }
            count += self.dispatch(env_id, "review.sla_breached", payload)
        return count
