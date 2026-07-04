"""API key lifecycle tests (contract §6): create shows the secret once,
reads return the prefix only, rotate with/without a grace window,
revoke semantics, scope enforcement, and audit side effects."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, store  # noqa: E402

ADMIN = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _env(kind: str = "sandbox") -> dict:
    org = client.post("/v1/orgs", json={"name": _name("org")}, headers=ADMIN).json()["data"]
    return client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": _name("env"), "kind": kind}, headers=ADMIN,
    ).json()["data"]


def _create_key(env_id: str, scope: str = "ingest", name: str = "ci-key") -> dict:
    r = client.post(
        "/v1/api-keys",
        json={"env_id": env_id, "name": name, "scope": scope}, headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _bearer(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def test_create_key_returns_secret_exactly_once():
    env = _env()
    created = _create_key(env["id"])
    assert created["id"].startswith("key_")
    assert created["secret"].startswith("pzq_sandbox_")
    assert created["secret_prefix"] == created["secret"][:12]

    listed = client.get(f"/v1/api-keys?env_id={env['id']}", headers=ADMIN).json()
    assert listed["meta"]["total"] == 1
    (row,) = listed["data"]
    assert "secret" not in row
    assert row["secret_prefix"] == created["secret_prefix"]


def test_production_env_key_prefix():
    env = _env(kind="production")
    created = _create_key(env["id"])
    assert created["secret"].startswith("pzq_production_")


def test_create_key_unknown_env_404():
    r = client.post(
        "/v1/api-keys",
        json={"env_id": "env_missing", "name": "x", "scope": "read"}, headers=ADMIN,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_ingest_scope_can_post_but_not_admin():
    env = _env()
    secret = _create_key(env["id"], scope="ingest")["secret"]
    span = {"name": "s", "trace_id": "t1", "span_id": uuid.uuid4().hex}
    ok = client.post("/v1/traces", json={"spans": [span]}, headers=_bearer(secret))
    assert ok.status_code == 200
    # developer role (mapped from ingest scope) may read, but not manage keys
    denied = client.post(
        "/v1/api-keys",
        json={"env_id": env["id"], "name": "nope", "scope": "read"},
        headers=_bearer(secret),
    )
    assert denied.status_code == 403


def test_read_scope_cannot_manage_keys():
    env = _env()
    secret = _create_key(env["id"], scope="read")["secret"]
    r = client.post(
        "/v1/api-keys",
        json={"env_id": env["id"], "name": "nope", "scope": "read"},
        headers=_bearer(secret),
    )
    assert r.status_code == 403


def test_missing_key_is_401():
    assert client.get("/v1/api-keys").status_code == 401


def test_rotate_without_grace_invalidates_old_secret():
    env = _env()
    created = _create_key(env["id"])
    old_secret = created["secret"]

    r = client.post(
        f"/v1/api-keys/{created['id']}/rotate", json={"grace_seconds": 0}, headers=ADMIN
    )
    assert r.status_code == 200
    rotated = r.json()["data"]
    new_secret = rotated["secret"]
    assert new_secret != old_secret
    assert rotated["rotated_at_ms"] is not None
    assert rotated["grace_until_ms"] is None

    span = {"name": "s", "trace_id": "t1", "span_id": uuid.uuid4().hex}
    assert client.post(
        "/v1/traces", json={"spans": [span]}, headers=_bearer(new_secret)
    ).status_code == 200
    assert client.post(
        "/v1/traces", json={"spans": [span]}, headers=_bearer(old_secret)
    ).status_code == 403


def test_rotate_with_grace_keeps_old_secret_valid():
    env = _env()
    created = _create_key(env["id"])
    old_secret = created["secret"]

    r = client.post(
        f"/v1/api-keys/{created['id']}/rotate", json={"grace_seconds": 3600}, headers=ADMIN
    )
    assert r.status_code == 200
    assert r.json()["data"]["grace_until_ms"] is not None

    span = {"name": "s", "trace_id": "t1", "span_id": uuid.uuid4().hex}
    assert client.post(
        "/v1/traces", json={"spans": [span]}, headers=_bearer(old_secret)
    ).status_code == 200


def test_rotate_unknown_key_404():
    r = client.post("/v1/api-keys/key_missing/rotate", json={}, headers=ADMIN)
    assert r.status_code == 404


def test_revoke_stops_validation_immediately():
    env = _env()
    created = _create_key(env["id"])

    r = client.delete(f"/v1/api-keys/{created['id']}", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["data"]["revoked_at_ms"] is not None

    span = {"name": "s", "trace_id": "t1", "span_id": uuid.uuid4().hex}
    assert client.post(
        "/v1/traces", json={"spans": [span]}, headers=_bearer(created["secret"])
    ).status_code == 403

    # Soft delete: the row is kept and still listable for audit resolution.
    listed = client.get(f"/v1/api-keys?env_id={env['id']}", headers=ADMIN).json()
    assert listed["meta"]["total"] == 1


def test_revoked_key_cannot_rotate_and_double_revoke_conflicts():
    env = _env()
    created = _create_key(env["id"])
    client.delete(f"/v1/api-keys/{created['id']}", headers=ADMIN)

    rotate = client.post(f"/v1/api-keys/{created['id']}/rotate", json={}, headers=ADMIN)
    assert rotate.status_code == 409
    again = client.delete(f"/v1/api-keys/{created['id']}", headers=ADMIN)
    assert again.status_code == 409


def test_key_lifecycle_writes_audit_log():
    env = _env()
    created = _create_key(env["id"])
    client.post(f"/v1/api-keys/{created['id']}/rotate", json={}, headers=ADMIN)
    client.delete(f"/v1/api-keys/{created['id']}", headers=ADMIN)
    rows = store.connection.execute(
        "SELECT action, detail FROM audit_log WHERE resource = ? ORDER BY id",
        (created["id"],),
    ).fetchall()
    assert [r[0] for r in rows] == ["api_key.create", "api_key.rotate", "api_key.revoke"]
    # The audit trail must never contain the plaintext secret.
    for _, detail in rows:
        assert created["secret"] not in detail
