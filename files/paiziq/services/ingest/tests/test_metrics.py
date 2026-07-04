"""Metrics API tests (PZ-079)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app  # noqa: E402

ADMIN = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def _env():
    org = client.post("/v1/orgs", json={"name": f"org-{uuid.uuid4().hex[:6]}"}, headers=ADMIN).json()["data"]
    return client.post(f"/v1/orgs/{org['id']}/environments", json={"name": "s", "kind": "sandbox"}, headers=ADMIN).json()["data"]


def test_metrics_summary_empty_env():
    env = _env()
    r = client.get(f"/v1/metrics/summary?env_id={env['id']}", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["env_id"] == env["id"]
    assert body["decisions"] == {}
