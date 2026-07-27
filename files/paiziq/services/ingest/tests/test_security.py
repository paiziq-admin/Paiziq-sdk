"""Security hardening tests (PZ-073/083/084)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app  # noqa: E402
from auth import actor_for  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402

ADMIN = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def test_rate_limiter_blocks_burst():
    limiter = RateLimiter(2)
    assert limiter.allow("k1")
    assert limiter.allow("k1")
    assert not limiter.allow("k1")


def test_bootstrap_audit_actors_are_stable_unique_fingerprints():
    first = actor_for("bootstrap-key-one")
    assert first == actor_for("bootstrap-key-one")
    assert first != actor_for("bootstrap-key-two")
    assert "bootstrap-key-one" not in first


def test_reviewer_role_can_read_audit():
    org = client.post("/v1/orgs", json={"name": f"org-{uuid.uuid4().hex[:6]}"}, headers=ADMIN).json()["data"]
    env = client.post(f"/v1/orgs/{org['id']}/environments", json={"name": "s", "kind": "sandbox"}, headers=ADMIN).json()["data"]
    key = client.post(
        "/v1/api-keys",
        json={"env_id": env["id"], "name": "rev", "scope": "read", "role": "reviewer"},
        headers=ADMIN,
    ).json()["data"]
    r = client.get("/v1/audit-logs", headers={"Authorization": f"Bearer {key['secret']}"})
    assert r.status_code == 200, r.text
