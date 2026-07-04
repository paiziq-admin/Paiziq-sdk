"""paiziq — command-line interface (PZ-039/PZ-040).

    paiziq init --endpoint URL [--api-key KEY] [--env ENV_ID]
    paiziq login --api-key KEY [--endpoint URL]
    paiziq agents list [--env ENV_ID] [--json]
    paiziq agents register --name NAME [--env ENV_ID] [--framework FW]
    paiziq keys list|create|rotate|revoke ...
    paiziq dashboard deploy [--dir PATH] | serve [--port PORT]
    paiziq replay TRACE_ID [--json]

Stdlib-only (argparse + the SDK's own transport). Configuration lives
in ~/.paiziq/config.json (PAIZIQ_CONFIG_DIR overrides).
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from .client import CliError, TransportFactory
from .commands import (
    cmd_agents_list,
    cmd_agents_register,
    cmd_init,
    cmd_keys_create,
    cmd_keys_list,
    cmd_keys_revoke,
    cmd_keys_rotate,
    cmd_login,
    cmd_replay,
)
from .config import ConfigError
from .dashboard import cmd_dashboard_deploy, cmd_dashboard_serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paiziq", description="Paiziq control-plane CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write CLI configuration")
    init.add_argument("--endpoint", required=True, help="backend base URL")
    init.add_argument("--api-key", default=None)
    init.add_argument("--env", default=None, help="default environment id")
    init.set_defaults(func=cmd_init)

    login = sub.add_parser("login", help="verify and store an API key")
    login.add_argument("--api-key", required=True)
    login.add_argument("--endpoint", default=None)
    login.set_defaults(func=cmd_login)

    agents = sub.add_parser("agents", help="agent registry").add_subparsers(
        dest="subcommand", required=True
    )
    agents_list = agents.add_parser("list")
    agents_list.add_argument("--env", default=None)
    agents_list.add_argument("--json", action="store_true")
    agents_list.set_defaults(func=cmd_agents_list)
    agents_register = agents.add_parser("register")
    agents_register.add_argument("--name", required=True)
    agents_register.add_argument("--env", default=None)
    agents_register.add_argument("--framework", default=None)
    agents_register.add_argument("--json", action="store_true")
    agents_register.set_defaults(func=cmd_agents_register)

    keys = sub.add_parser("keys", help="API key lifecycle").add_subparsers(
        dest="subcommand", required=True
    )
    keys_list = keys.add_parser("list")
    keys_list.add_argument("--env", default=None)
    keys_list.add_argument("--json", action="store_true")
    keys_list.set_defaults(func=cmd_keys_list)
    keys_create = keys.add_parser("create")
    keys_create.add_argument("--name", required=True)
    keys_create.add_argument("--env", default=None)
    keys_create.add_argument("--scope", required=True, choices=["ingest", "read", "admin"])
    keys_create.add_argument("--json", action="store_true")
    keys_create.set_defaults(func=cmd_keys_create)
    keys_rotate = keys.add_parser("rotate")
    keys_rotate.add_argument("key_id")
    keys_rotate.add_argument("--grace-seconds", type=int, default=0)
    keys_rotate.add_argument("--json", action="store_true")
    keys_rotate.set_defaults(func=cmd_keys_rotate)
    keys_revoke = keys.add_parser("revoke")
    keys_revoke.add_argument("key_id")
    keys_revoke.add_argument("--json", action="store_true")
    keys_revoke.set_defaults(func=cmd_keys_revoke)

    dashboard = sub.add_parser("dashboard", help="local dashboard").add_subparsers(
        dest="subcommand", required=True
    )
    deploy = dashboard.add_parser("deploy")
    deploy.add_argument("--dir", default="paiziq-dashboard")
    deploy.set_defaults(func=cmd_dashboard_deploy)
    serve = dashboard.add_parser("serve")
    serve.add_argument("--port", type=int, default=8900)
    serve.set_defaults(func=cmd_dashboard_serve)

    replay = sub.add_parser("replay", help="pretty-print a trace's span tree")
    replay.add_argument("trace_id")
    replay.add_argument("--json", action="store_true")
    replay.set_defaults(func=cmd_replay)

    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    transport_factory: Optional[TransportFactory] = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args, transport_factory))
    except (CliError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
