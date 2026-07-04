"""Lightweight metrics rollup job (PZ-079)."""

from __future__ import annotations

import sqlite3
import threading

from ids import now_ms

_BUCKET_MS = 3_600_000


def run_rollups(conn: sqlite3.Connection, lock: threading.Lock) -> int:
    end = now_ms()
    start = end - 7 * 86_400_000
    bucket_start = (start // _BUCKET_MS) * _BUCKET_MS
    upserted = 0
    with lock:
        rows = conn.execute(
            "SELECT p.env_id, (d.created_at_ms / ?) * ?, d.verdict, COUNT(*) "
            "FROM decisions d JOIN payments p ON p.id = d.payment_id "
            "WHERE d.created_at_ms >= ? GROUP BY 1, 2, 3",
            (_BUCKET_MS, _BUCKET_MS, bucket_start),
        ).fetchall()
        for env_id, bucket_ms, verdict, count in rows:
            metric = f"decisions.{verdict}"
            conn.execute(
                "INSERT INTO metrics_rollups (env_id, bucket_ms, metric, value) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(env_id, bucket_ms, metric) DO UPDATE SET value = excluded.value",
                (env_id, bucket_ms, metric, float(count)),
            )
            upserted += 1
        conn.commit()
    return upserted
