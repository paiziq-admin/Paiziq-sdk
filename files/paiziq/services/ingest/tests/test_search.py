"""Event search tests (PZ-080)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app  # noqa: E402

AUTH = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def test_search_finds_indexed_span_event():
    trace = f"tr-{uuid.uuid4().hex[:8]}"
    span = {
        "name": "paiziq.review_payment",
        "trace_id": trace,
        "span_id": uuid.uuid4().hex,
        "start_ms": 1000,
        "events": [{"name": "decision", "ts_ms": 1001, "attributes": {"merchant": "acme unique token"}}],
    }
    assert client.post("/v1/traces", json={"spans": [span]}, headers=AUTH).status_code == 200
    r = client.get(f"/v1/search/events?trace_id={trace}", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["total"] >= 1
