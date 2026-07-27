"""Organization/environment API tests: envelope shape, CRUD, pagination,
conflicts, auth, and audit-log side effects."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, store  # noqa: E402
from auth import actor_for  # noqa: E402

AUTH = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _create_org(name: str | None = None) -> dict:
    r = client.post("/v1/orgs", json={"name": name or _name("org")}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_create_org_returns_envelope():
    name = _name("acme")
    r = client.post("/v1/orgs", json={"name": name}, headers=AUTH)
    body = r.json()
    assert r.status_code == 200
    assert body["success"] is True and body["error"] is None
    assert body["data"]["name"] == name
    assert body["data"]["id"].startswith("org_")
    assert isinstance(body["data"]["created_at_ms"], int)


def test_org_requires_auth():
    assert client.post("/v1/orgs", json={"name": _name("x")}).status_code == 401
    assert client.get("/v1/orgs").status_code == 401


def test_duplicate_org_name_conflicts():
    name = _name("dup")
    _create_org(name)
    r = client.post("/v1/orgs", json={"name": name}, headers=AUTH)
    body = r.json()
    assert r.status_code == 409
    assert body["success"] is False and body["data"] is None
    assert body["error"]["code"] == "conflict"


def test_get_org_and_not_found():
    org = _create_org()
    assert client.get(f"/v1/orgs/{org['id']}", headers=AUTH).json()["data"] == org
    r = client.get("/v1/orgs/org_missing", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_list_orgs_paginates_with_meta():
    _create_org()
    r = client.get("/v1/orgs?limit=1&offset=0", headers=AUTH)
    body = r.json()
    assert len(body["data"]) == 1
    assert body["meta"]["limit"] == 1 and body["meta"]["offset"] == 0
    assert body["meta"]["total"] >= 1


def test_invalid_org_name_rejected():
    assert client.post("/v1/orgs", json={"name": ""}, headers=AUTH).status_code == 422


def test_environment_lifecycle():
    org = _create_org()
    r = client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "sandbox", "kind": "sandbox"}, headers=AUTH,
    )
    env = r.json()["data"]
    assert r.status_code == 200
    assert env["id"].startswith("env_") and env["org_id"] == org["id"]
    # duplicate name in same org conflicts
    r = client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "sandbox", "kind": "production"}, headers=AUTH,
    )
    assert r.status_code == 409
    # same name in a different org is fine
    other = _create_org()
    r = client.post(
        f"/v1/orgs/{other['id']}/environments",
        json={"name": "sandbox", "kind": "sandbox"}, headers=AUTH,
    )
    assert r.status_code == 200
    # listing scoped to org
    listed = client.get(f"/v1/orgs/{org['id']}/environments", headers=AUTH).json()
    assert [e["id"] for e in listed["data"]] == [env["id"]]
    assert listed["meta"]["total"] == 1


def test_environment_invalid_kind_rejected():
    org = _create_org()
    r = client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "e", "kind": "staging"}, headers=AUTH,
    )
    assert r.status_code == 422


def test_environment_unknown_org_404():
    r = client.post(
        "/v1/orgs/org_missing/environments",
        json={"name": "e", "kind": "sandbox"}, headers=AUTH,
    )
    assert r.status_code == 404


def test_mutations_write_audit_log():
    org = _create_org()
    rows = store.connection.execute(
        "SELECT actor, action FROM audit_log WHERE resource = ?", (org["id"],)
    ).fetchall()
    assert rows == [(actor_for("dev-key"), "org.create")]
