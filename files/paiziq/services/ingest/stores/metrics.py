"""Metrics queries for dashboard trends (PZ-079)."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from ids import now_ms

_INTERVAL_MS = {"1h": 3_600_000, "1d": 86_400_000}


class MetricsStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def summary(
        self,
        env_id: str,
        from_ms: Optional[int] = None,
        to_ms: Optional[int] = None,
    ) -> dict[str, Any]:
        end = to_ms if to_ms is not None else now_ms()
        start = from_ms if from_ms is not None else end - 86_400_000
        with self._lock:
            decisions = self._conn.execute(
                "SELECT d.verdict, COUNT(*) FROM decisions d "
                "JOIN payments p ON p.id = d.payment_id "
                "WHERE p.env_id = ? AND d.created_at_ms BETWEEN ? AND ? "
                "GROUP BY d.verdict",
                (env_id, start, end),
            ).fetchall()
            decision_risks = self._conn.execute(
                "SELECT d.risk_flags FROM decisions d "
                "JOIN payments p ON p.id = d.payment_id "
                "WHERE p.env_id = ? AND d.created_at_ms BETWEEN ? AND ?",
                (env_id, start, end),
            ).fetchall()
            payments = self._conn.execute(
                "SELECT state, COUNT(*) FROM payments "
                "WHERE env_id = ? AND created_at_ms BETWEEN ? AND ? GROUP BY state",
                (env_id, start, end),
            ).fetchall()
            open_reviews = self._conn.execute(
                "SELECT COUNT(*) FROM reviews r JOIN payments p ON p.id = r.payment_id "
                "WHERE p.env_id = ? AND r.state = 'open'",
                (env_id,),
            ).fetchone()[0]
            wh_total = self._conn.execute(
                "SELECT COUNT(*) FROM webhook_deliveries wd "
                "JOIN webhook_endpoints we ON we.id = wd.endpoint_id "
                "WHERE we.env_id = ? AND wd.created_at_ms BETWEEN ? AND ?",
                (env_id, start, end),
            ).fetchone()[0]
            wh_delivered = self._conn.execute(
                "SELECT COUNT(*) FROM webhook_deliveries wd "
                "JOIN webhook_endpoints we ON we.id = wd.endpoint_id "
                "WHERE we.env_id = ? AND wd.state = 'delivered' "
                "AND wd.created_at_ms BETWEEN ? AND ?",
                (env_id, start, end),
            ).fetchone()[0]
        risk_flags: dict[str, int] = {}
        for (encoded_flags,) in decision_risks:
            for flag in json.loads(encoded_flags or "[]"):
                risk_flags[flag] = risk_flags.get(flag, 0) + 1
        return {
            "env_id": env_id,
            "from_ms": start,
            "to_ms": end,
            "decisions": {v: c for v, c in decisions},
            "risk_flags": risk_flags,
            "payments": {s: c for s, c in payments},
            "open_reviews": open_reviews,
            "webhook_deliveries": wh_total,
            "webhook_delivered": wh_delivered,
            "webhook_success_rate": (
                round(wh_delivered / wh_total, 4) if wh_total else None
            ),
        }

    def timeseries(
        self,
        env_id: str,
        metric: str,
        interval: str,
        from_ms: Optional[int] = None,
        to_ms: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        bucket = _INTERVAL_MS.get(interval, _INTERVAL_MS["1h"])
        end = to_ms if to_ms is not None else now_ms()
        start = from_ms if from_ms is not None else end - 7 * 86_400_000
        with self._lock:
            rows = self._conn.execute(
                "SELECT bucket_ms, value FROM metrics_rollups "
                "WHERE env_id = ? AND metric = ? AND bucket_ms BETWEEN ? AND ? "
                "ORDER BY bucket_ms",
                (env_id, metric, start, end),
            ).fetchall()
        if rows:
            return [{"bucket_ms": r[0], "value": r[1]} for r in rows]
        if metric.startswith("decisions."):
            verdict = metric.split(".", 1)[1]
            with self._lock:
                rows = self._conn.execute(
                    "SELECT (d.created_at_ms / ?) * ?, COUNT(*) FROM decisions d "
                    "JOIN payments p ON p.id = d.payment_id "
                    "WHERE p.env_id = ? AND d.verdict = ? "
                    "AND d.created_at_ms BETWEEN ? AND ? GROUP BY 1 ORDER BY 1",
                    (bucket, bucket, env_id, verdict, start, end),
                ).fetchall()
            return [{"bucket_ms": r[0], "value": float(r[1])} for r in rows]
        if metric.startswith("payments."):
            state = metric.split(".", 1)[1]
            state_clause = "" if state == "total" else "AND state = ? "
            params: tuple[Any, ...] = (
                (bucket, bucket, env_id, start, end)
                if state == "total"
                else (bucket, bucket, env_id, state, start, end)
            )
            with self._lock:
                rows = self._conn.execute(
                    "SELECT (created_at_ms / ?) * ?, COUNT(*) FROM payments "
                    "WHERE env_id = ? "
                    f"{state_clause}"
                    "AND created_at_ms BETWEEN ? AND ? GROUP BY 1 ORDER BY 1",
                    params,
                ).fetchall()
            return [{"bucket_ms": r[0], "value": float(r[1])} for r in rows]
        return []
