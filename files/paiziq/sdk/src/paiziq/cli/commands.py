"""Command handlers for the paiziq CLI (PZ-040).

Each handler takes parsed argparse args plus an injected transport
factory (tests pass fakes) and returns a process exit code. Output is
human-readable by default; `--json` prints raw API data.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .client import ApiClient, TransportFactory
from .config import load_config, save_config


def _emit(data: Any, as_json: bool, human: str) -> None:
    print(json.dumps(data, indent=2) if as_json else human)


def _client(args: Any, factory: Optional[TransportFactory]) -> ApiClient:
    return ApiClient(load_config(), transport_factory=factory)


def cmd_init(args: Any, factory: Optional[TransportFactory] = None) -> int:
    config = load_config().merged(
        endpoint=args.endpoint, api_key=args.api_key, env_id=args.env
    )
    path = save_config(config)
    print(f"wrote {path}")
    if not config.api_key:
        print("next: paiziq login --api-key <key>")
    return 0


def cmd_login(args: Any, factory: Optional[TransportFactory] = None) -> int:
    config = load_config().merged(endpoint=args.endpoint, api_key=args.api_key)
    # verify the key before persisting it: any read endpoint will do
    ApiClient(config, transport_factory=factory).get("/v1/agents?limit=1")
    save_config(config)
    print(f"logged in against {config.require_endpoint()} (key …{args.api_key[-4:]})")
    return 0


def cmd_agents_list(args: Any, factory: Optional[TransportFactory] = None) -> int:
    query = f"?env_id={args.env}" if args.env else ""
    agents = _client(args, factory).get(f"/v1/agents{query}")
    lines = [
        f"{a['id']}  {a['status']:<8}  {a['name']}  ({a.get('framework') or '-'})"
        for a in agents
    ] or ["no agents"]
    _emit(agents, args.json, "\n".join(lines))
    return 0


def cmd_agents_register(args: Any, factory: Optional[TransportFactory] = None) -> int:
    env_id = args.env or load_config().env_id
    if not env_id:
        print("error: --env required (no default env in config)")
        return 1
    agent = _client(args, factory).post(
        "/v1/agents",
        {"env_id": env_id, "name": args.name, "framework": args.framework},
    )
    _emit(agent, args.json, f"registered {agent['id']} ({agent['name']})")
    return 0


def cmd_keys_list(args: Any, factory: Optional[TransportFactory] = None) -> int:
    query = f"?env_id={args.env}" if args.env else ""
    keys = _client(args, factory).get(f"/v1/api-keys{query}")
    lines = [
        f"{k['id']}  {k['scope']:<6}  {k['secret_prefix']}…  "
        f"{'revoked' if k['revoked_at_ms'] else 'active'}  {k['name']}"
        for k in keys
    ] or ["no keys"]
    _emit(keys, args.json, "\n".join(lines))
    return 0


def cmd_keys_create(args: Any, factory: Optional[TransportFactory] = None) -> int:
    env_id = args.env or load_config().env_id
    if not env_id:
        print("error: --env required (no default env in config)")
        return 1
    key = _client(args, factory).post(
        "/v1/api-keys", {"env_id": env_id, "name": args.name, "scope": args.scope}
    )
    _emit(
        key, args.json,
        f"created {key['id']} ({key['scope']})\n"
        f"secret (shown once, store it now): {key['secret']}",
    )
    return 0


def cmd_keys_rotate(args: Any, factory: Optional[TransportFactory] = None) -> int:
    key = _client(args, factory).post(
        f"/v1/api-keys/{args.key_id}/rotate", {"grace_seconds": args.grace_seconds}
    )
    _emit(
        key, args.json,
        f"rotated {key['id']} (old secret valid {args.grace_seconds}s)\n"
        f"secret (shown once, store it now): {key['secret']}",
    )
    return 0


def cmd_keys_revoke(args: Any, factory: Optional[TransportFactory] = None) -> int:
    key = _client(args, factory).delete(f"/v1/api-keys/{args.key_id}")
    _emit(key, args.json, f"revoked {key['id']}")
    return 0


def _span_tree(spans: list[dict[str, Any]]) -> list[str]:
    children: dict[Optional[str], list[dict[str, Any]]] = {}
    for span in spans:
        children.setdefault(span.get("parent_span_id"), []).append(span)

    lines: list[str] = []

    def render(parent: Optional[str], depth: int) -> None:
        for span in sorted(children.get(parent, []), key=lambda s: s.get("start_ms") or 0):
            duration = span.get("duration_ms")
            suffix = f" [{duration}ms]" if duration is not None else ""
            lines.append(f"{'  ' * depth}• {span['name']} ({span['status']}){suffix}")
            for event in span.get("events", []):
                lines.append(f"{'  ' * (depth + 1)}⚑ {event.get('name', 'event')}")
            render(span["span_id"], depth + 1)

    render(None, 0)
    return lines


def cmd_replay(args: Any, factory: Optional[TransportFactory] = None) -> int:
    body = _client(args, factory).get(f"/v1/traces/{args.trace_id}")
    spans = body.get("spans", [])
    if not spans:
        print(f"no spans recorded for trace {args.trace_id}")
        return 1
    _emit(
        body, args.json,
        f"trace {args.trace_id} — {len(spans)} span(s)\n" + "\n".join(_span_tree(spans)),
    )
    return 0
