"""Agent API tests: idempotent registration, metadata patch semantics,
filters, conflicts, and audit side effects."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, store  # noqa: E402

AUTH = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _env() -> dict:
    org = client.post("/v1/orgs", json={"name": _name("org")}, headers=AUTH).json()["data"]
    return client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "sandbox", "kind": "sandbox"}, headers=AUTH,
    ).json()["data"]


def _register(env_id: str, name: str, **kw) -> dict:
    body = {"env_id": env_id, "name": name, **kw}
    r = client.post("/v1/agents", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_register_agent():
    env = _env()
    agent = _register(env["id"], "procurement-agent", framework="langchain",
                      metadata={"owner": "team-payments"})
    assert agent["id"].startswith("agt_")
    assert agent["status"] == "active"
    assert agent["framework"] == "langchain"
    assert agent["metadata"] == {"owner": "team-payments"}


def test_register_is_idempotent_on_env_and_name():
    env = _env()
    first = _register(env["id"], "boot-agent")
    second = _register(env["id"], "boot-agent", metadata={"ignored": True})
    assert second == first  # existing agent returned untouched


def test_register_unknown_env_404():
    r = client.post(
        "/v1/agents", json={"env_id": "env_missing", "name": "a"}, headers=AUTH
    )
    assert r.status_code == 404


def test_list_agents_filters_by_env():
    env_a, env_b = _env(), _env()
    agent = _register(env_a["id"], "only-in-a")
    _register(env_b["id"], "only-in-b")
    listed = client.get(f"/v1/agents?env_id={env_a['id']}", headers=AUTH).json()
    assert [a["id"] for a in listed["data"]] == [agent["id"]]
    assert listed["meta"]["total"] == 1


def test_patch_agent_fields_and_metadata_replacement():
    env = _env()
    agent = _register(env["id"], "patch-me", metadata={"a": 1, "b": 2})
    r = client.patch(
        f"/v1/agents/{agent['id']}",
        json={"status": "disabled", "metadata": {"c": 3}}, headers=AUTH,
    )
    updated = r.json()["data"]
    assert r.status_code == 200
    assert updated["status"] == "disabled"
    assert updated["metadata"] == {"c": 3}  # full replacement, not merge
    assert updated["name"] == "patch-me"


def test_patch_rename_conflict():
    env = _env()
    _register(env["id"], "taken")
    other = _register(env["id"], "renamer")
    r = client.patch(f"/v1/agents/{other['id']}", json={"name": "taken"}, headers=AUTH)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_patch_unknown_agent_404():
    r = client.patch("/v1/agents/agt_missing", json={"status": "disabled"}, headers=AUTH)
    assert r.status_code == 404


def test_get_agent_roundtrip():
    env = _env()
    agent = _register(env["id"], "reader")
    assert client.get(f"/v1/agents/{agent['id']}", headers=AUTH).json()["data"] == agent


def test_agent_mutations_write_audit_log():
    env = _env()
    agent = _register(env["id"], "audited")
    client.patch(f"/v1/agents/{agent['id']}", json={"status": "disabled"}, headers=AUTH)
    rows = store.connection.execute(
        "SELECT action FROM audit_log WHERE resource = ? ORDER BY id", (agent["id"],)
    ).fetchall()
    assert [r[0] for r in rows] == ["agent.register", "agent.update"]
