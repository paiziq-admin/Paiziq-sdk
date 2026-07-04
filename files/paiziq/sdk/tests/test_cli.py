"""CLI tests (PZ-039/PZ-040): config lifecycle, command round-trips
through a fake transport, error surfacing, and the dashboard bundle."""

from __future__ import annotations

import json
import stat

import pytest

from paiziq.cli import main
from paiziq.cli.config import load_config
from paiziq.cli.dashboard import write_bundle
from paiziq.transport import TransportResponse


class FakeTransport:
    """Records requests; replies from a canned {(method, path): body} map."""

    def __init__(self, responses: dict[tuple[str, str], dict]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, json_body=None, headers=None):
        self.requests.append((method, path, json_body))
        body = self.responses.get((method, path))
        if body is None:
            return TransportResponse(status=404, body=json.dumps(
                {"success": False, "data": None,
                 "error": {"code": "not_found", "message": path}}).encode(), headers={})
        return TransportResponse(status=200, body=json.dumps(body).encode(), headers={})


def _factory(fake: FakeTransport):
    def factory(endpoint: str, api_key: str):
        fake.endpoint, fake.api_key = endpoint, api_key
        return fake
    return factory


def _envelope(data) -> dict:
    return {"success": True, "data": data, "error": None}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIZIQ_CONFIG_DIR", str(tmp_path / "paiziq-config"))
    return tmp_path


def test_init_writes_config_with_owner_only_perms(tmp_path, capsys):
    assert main(["init", "--endpoint", "http://127.0.0.1:8800", "--env", "env_1"]) == 0
    config = load_config()
    assert config.endpoint == "http://127.0.0.1:8800"
    assert config.env_id == "env_1"
    path = tmp_path / "paiziq-config" / "config.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "paiziq login" in capsys.readouterr().out  # next-step hint


def test_login_verifies_key_before_saving(capsys):
    main(["init", "--endpoint", "http://127.0.0.1:8800"])
    fake = FakeTransport({("GET", "/v1/agents?limit=1"): _envelope([])})
    assert main(["login", "--api-key", "pzq_sandbox_abc123"],
                transport_factory=_factory(fake)) == 0
    assert load_config().api_key == "pzq_sandbox_abc123"
    assert "…c123" in capsys.readouterr().out  # only a suffix is echoed


def test_login_failure_does_not_save_key(capsys):
    main(["init", "--endpoint", "http://127.0.0.1:8800"])
    fake = FakeTransport({})  # every path 404s
    assert main(["login", "--api-key", "bad-key"],
                transport_factory=_factory(fake)) == 1
    assert load_config().api_key is None
    assert "error:" in capsys.readouterr().err


def _logged_in(fake: FakeTransport) -> None:
    main(["init", "--endpoint", "http://127.0.0.1:8800", "--env", "env_1"])
    fake.responses[("GET", "/v1/agents?limit=1")] = _envelope([])
    main(["login", "--api-key", "pzq_sandbox_abc123"], transport_factory=_factory(fake))


def test_agents_register_and_list(capsys):
    fake = FakeTransport({})
    _logged_in(fake)
    agent = {"id": "agt_1", "env_id": "env_1", "name": "bot", "framework": "langchain",
             "status": "active", "metadata": {}, "created_at_ms": 1}
    fake.responses[("POST", "/v1/agents")] = _envelope(agent)
    fake.responses[("GET", "/v1/agents?env_id=env_1")] = _envelope([agent])

    assert main(["agents", "register", "--name", "bot", "--framework", "langchain"],
                transport_factory=_factory(fake)) == 0
    method, path, body = fake.requests[-1]
    assert (method, path) == ("POST", "/v1/agents")
    assert body["env_id"] == "env_1"  # default env from config

    assert main(["agents", "list", "--env", "env_1"],
                transport_factory=_factory(fake)) == 0
    out = capsys.readouterr().out
    assert "agt_1" in out and "bot" in out


def test_keys_create_prints_secret_once(capsys):
    fake = FakeTransport({})
    _logged_in(fake)
    fake.responses[("POST", "/v1/api-keys")] = _envelope({
        "id": "key_1", "scope": "ingest", "secret_prefix": "pzq_sandbox_",
        "secret": "pzq_sandbox_fullsecret", "revoked_at_ms": None, "name": "ci",
    })
    assert main(["keys", "create", "--name", "ci", "--scope", "ingest"],
                transport_factory=_factory(fake)) == 0
    out = capsys.readouterr().out
    assert "pzq_sandbox_fullsecret" in out and "shown once" in out


def test_keys_rotate_and_revoke(capsys):
    fake = FakeTransport({})
    _logged_in(fake)
    fake.responses[("POST", "/v1/api-keys/key_1/rotate")] = _envelope(
        {"id": "key_1", "secret": "pzq_sandbox_next"})
    fake.responses[("DELETE", "/v1/api-keys/key_1")] = _envelope(
        {"id": "key_1", "revoked_at_ms": 5})
    assert main(["keys", "rotate", "key_1", "--grace-seconds", "60"],
                transport_factory=_factory(fake)) == 0
    assert fake.requests[-1][2] == {"grace_seconds": 60}
    assert main(["keys", "revoke", "key_1"], transport_factory=_factory(fake)) == 0
    assert "revoked key_1" in capsys.readouterr().out


def test_replay_renders_span_tree(capsys):
    fake = FakeTransport({})
    _logged_in(fake)
    fake.responses[("GET", "/v1/traces/tr1")] = {
        "trace_id": "tr1",
        "spans": [
            {"name": "paiziq.review_payment", "span_id": "s1", "parent_span_id": None,
             "start_ms": 1, "duration_ms": 3, "status": "ok",
             "events": [{"name": "decision"}]},
            {"name": "paiziq.execute_payment", "span_id": "s2", "parent_span_id": "s1",
             "start_ms": 2, "duration_ms": 1, "status": "ok", "events": []},
        ],
    }
    assert main(["replay", "tr1"], transport_factory=_factory(fake)) == 0
    out = capsys.readouterr().out
    assert "paiziq.review_payment" in out
    assert "  • paiziq.execute_payment" in out  # nested under parent
    assert "⚑ decision" in out


def test_replay_missing_trace_fails(capsys):
    fake = FakeTransport({})
    _logged_in(fake)
    fake.responses[("GET", "/v1/traces/none")] = {"trace_id": "none", "spans": []}
    assert main(["replay", "none"], transport_factory=_factory(fake)) == 1


def test_dashboard_deploy_writes_static_bundle(tmp_path, capsys):
    target = tmp_path / "dash"
    assert main(["dashboard", "deploy", "--dir", str(target)]) == 0
    page = (target / "index.html").read_text()
    assert "/api/v1/notifications" in page
    assert "pzq_" not in page  # never embeds a secret


def test_dashboard_bundle_helper(tmp_path):
    page = write_bundle(tmp_path / "d")
    assert page.name == "index.html" and page.exists()




def test_keys_list(capsys):
    fake = FakeTransport({})
    _logged_in(fake)
    fake.responses[("GET", "/v1/api-keys?env_id=env_1")] = _envelope([
        {"id": "key_1", "scope": "read", "secret_prefix": "pzq_sandbox_",
         "revoked_at_ms": None, "name": "ci"},
    ])
    assert main(["keys", "list", "--env", "env_1"], transport_factory=_factory(fake)) == 0
    assert "key_1" in capsys.readouterr().out


def test_agents_list_json(capsys):
    fake = FakeTransport({})
    _logged_in(fake)
    fake.responses[("GET", "/v1/agents?env_id=env_1")] = _envelope([])
    assert main(["agents", "list", "--env", "env_1", "--json"], transport_factory=_factory(fake)) == 0
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "[]"


def test_dashboard_serve_proxy(capsys):
    fake = FakeTransport({})
    _logged_in(fake)
    fake.responses[("GET", "/v1/notifications")] = {"notifications": []}
    from paiziq.cli.dashboard import serve
    import threading
    import urllib.request
    server = serve(0, transport_factory=_factory(fake))
    host, port = server.server_address[:2]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    with urllib.request.urlopen(f"http://{host}:{port}/api/v1/notifications") as resp:
        assert resp.status == 200
    server.shutdown()
    assert any(r[0] == "GET" and r[1] == "/v1/notifications" for r in fake.requests)

def test_commands_require_configuration(capsys):
    assert main(["agents", "list"]) == 1
    assert "no endpoint configured" in capsys.readouterr().err
