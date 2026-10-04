"""Internal event fan-out to webhook subscribers (PZ-078)."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from ids import new_id, now_ms
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

    def publish_execution_events(self, limit: int = 100) -> int:
        """Publish durable business events once per endpoint, in one transaction.

        Delivery itself is at least once. Consumers deduplicate the stable event
        ID; retrying this publisher cannot enqueue a second endpoint delivery.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT event_id,org_id,env_id,request_id,event_type,recorded_at_ms,payload_json "
                    "FROM execution_events e WHERE NOT EXISTS (SELECT 1 FROM execution_outbox_ack a "
                    "WHERE a.event_id=e.event_id AND a.consumer='webhooks') ORDER BY sequence LIMIT ?",
                    (limit,),
                ).fetchall()
                for (
                    event_id,
                    org_id,
                    env_id,
                    request_id,
                    kind,
                    timestamp,
                    payload,
                ) in rows:
                    payment = self._conn.execute(
                        "SELECT payment_id FROM execution_bindings WHERE org_id=? AND env_id=? AND request_id=?",
                        (org_id, env_id, request_id),
                    ).fetchone()
                    data = {
                        "request_id": request_id,
                        "payment_id": payment[0] if payment else request_id,
                        "org_id": org_id,
                        "env_id": env_id,
                        "evidence": json.loads(payload),
                    }
                    envelope = {
                        "id": event_id,
                        "type": kind,
                        "created_at_ms": timestamp,
                        "data": data,
                    }
                    endpoints = self._conn.execute(
                        "SELECT id,events FROM webhook_endpoints WHERE env_id=? AND status='active'",
                        (env_id,),
                    ).fetchall()
                    for endpoint_id, subscriptions in endpoints:
                        subscribed = json.loads(subscriptions)
                        if "*" not in subscribed and kind not in subscribed:
                            continue
                        ts = now_ms()
                        self._conn.execute(
                            "INSERT OR IGNORE INTO webhook_deliveries "
                            "(id,endpoint_id,event_type,payload,state,attempts,next_attempt_ms,created_at_ms,updated_at_ms,source_event_id) "
                            "VALUES(?,?,?,?,'pending',0,?,?,?,?)",
                            (
                                new_id("whd"),
                                endpoint_id,
                                kind,
                                json.dumps(envelope),
                                ts,
                                ts,
                                ts,
                                event_id,
                            ),
                        )
                    self._conn.execute(
                        "INSERT OR IGNORE INTO execution_outbox_ack VALUES(?,'webhooks',?)",
                        (event_id, now_ms()),
                    )
                self._conn.commit()
                return len(rows)
            except BaseException:
                self._conn.rollback()
                raise

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
