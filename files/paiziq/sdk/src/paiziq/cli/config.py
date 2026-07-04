"""CLI configuration: ~/.paiziq/config.json (PZ-040).

Holds the backend endpoint, the API key saved by `paiziq login`, and an
optional default environment id. The file is chmod 0600 because it
contains a secret; PAIZIQ_CONFIG_DIR overrides the location (tests,
multi-profile setups).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

_FILE_NAME = "config.json"


class ConfigError(RuntimeError):
    """Raised when the CLI configuration is missing or unusable."""


@dataclass(frozen=True)
class CliConfig:
    endpoint: Optional[str] = None
    api_key: Optional[str] = None
    env_id: Optional[str] = None

    def require_endpoint(self) -> str:
        if not self.endpoint:
            raise ConfigError("no endpoint configured — run `paiziq init --endpoint URL`")
        return self.endpoint

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError("no API key configured — run `paiziq login --api-key KEY`")
        return self.api_key

    def merged(self, **updates: Optional[str]) -> "CliConfig":
        """New config with the non-None updates applied (immutably)."""
        provided = {k: v for k, v in updates.items() if v is not None}
        return replace(self, **provided)


def config_dir() -> Path:
    override = os.getenv("PAIZIQ_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".paiziq"


def load_config() -> CliConfig:
    path = config_dir() / _FILE_NAME
    if not path.exists():
        return CliConfig()
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ConfigError(f"unreadable config at {path}: {exc}") from exc
    return CliConfig(
        endpoint=raw.get("endpoint"),
        api_key=raw.get("api_key"),
        env_id=raw.get("env_id"),
    )


def save_config(config: CliConfig) -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _FILE_NAME
    payload = {
        "endpoint": config.endpoint,
        "api_key": config.api_key,
        "env_id": config.env_id,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(path, 0o600)  # the file holds a secret
    return path
