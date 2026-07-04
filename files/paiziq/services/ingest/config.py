"""Environment-driven configuration for the ingest service."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Optional

DEV_KEY = "dev-key"
_ENVIRONMENTS = ("development", "production")


class ConfigError(ValueError):
    """Raised at startup when the environment is misconfigured."""


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    database_path: str = ":memory:"
    api_keys: frozenset[str] = field(default_factory=lambda: frozenset({DEV_KEY}))
    max_body_bytes: int = 1_000_000
    max_spans_per_batch: int = 500
    log_level: str = "INFO"
    rate_limit_rpm: int = 600
    cors_origins: frozenset[str] = field(default_factory=frozenset)
    secrets_key: Optional[str] = None
    review_sla_ms: int = 86_400_000
    retention_spans_days: Optional[int] = None
    retention_notifications_days: Optional[int] = None
    retention_audit_days: Optional[int] = None
    worker_interval_s: float = 1.0

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def _parse_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value


def _parse_optional_int(env: Mapping[str, str], name: str) -> Optional[int]:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _parse_keys(env: Mapping[str, str]) -> frozenset[str]:
    raw = env.get("PAIZIQ_INGEST_KEYS", DEV_KEY)
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def _parse_origins(env: Mapping[str, str]) -> frozenset[str]:
    raw = env.get("PAIZIQ_CORS_ORIGINS", "")
    return frozenset(o.strip() for o in raw.split(",") if o.strip())


def _validate_production(settings: Settings) -> None:
    problems: list[str] = []
    if settings.database_path == ":memory:":
        problems.append("PAIZIQ_INGEST_DB must be a persistent path (not :memory:)")
    if not settings.api_keys:
        problems.append("PAIZIQ_INGEST_KEYS must list at least one API key")
    if DEV_KEY in settings.api_keys:
        problems.append(f"PAIZIQ_INGEST_KEYS must not include the development key {DEV_KEY!r}")
    if problems:
        raise ConfigError("Invalid production configuration: " + "; ".join(problems))


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    if env is None:
        import os
        env = os.environ
    environment = env.get("PAIZIQ_ENV", "development").strip().lower()
    if environment not in _ENVIRONMENTS:
        raise ConfigError(f"PAIZIQ_ENV must be one of {_ENVIRONMENTS}, got {environment!r}")
    log_level = env.get("PAIZIQ_LOG_LEVEL", "INFO").strip().upper()
    if not isinstance(logging.getLevelName(log_level), int):
        raise ConfigError(f"PAIZIQ_LOG_LEVEL is not a valid logging level: {log_level!r}")
    default_rpm = 120 if environment == "production" else 600
    settings = Settings(
        environment=environment,
        database_path=env.get("PAIZIQ_INGEST_DB", ":memory:").strip() or ":memory:",
        api_keys=_parse_keys(env),
        max_body_bytes=_parse_int(env, "PAIZIQ_MAX_BODY_BYTES", 1_000_000),
        max_spans_per_batch=_parse_int(env, "PAIZIQ_MAX_SPANS_PER_BATCH", 500),
        log_level=log_level,
        rate_limit_rpm=_parse_int(env, "PAIZIQ_RATE_LIMIT_RPM", default_rpm),
        cors_origins=_parse_origins(env),
        secrets_key=(env.get("PAIZIQ_SECRETS_KEY") or None),
        review_sla_ms=_parse_int(env, "PAIZIQ_REVIEW_SLA_MS", 86_400_000),
        retention_spans_days=_parse_optional_int(env, "PAIZIQ_RETENTION_SPANS_DAYS"),
        retention_notifications_days=_parse_optional_int(env, "PAIZIQ_RETENTION_NOTIFICATIONS_DAYS"),
        retention_audit_days=_parse_optional_int(env, "PAIZIQ_RETENTION_AUDIT_DAYS"),
        worker_interval_s=float(env.get("PAIZIQ_WORKER_INTERVAL_S", "1.0") or "1.0"),
    )
    if settings.is_production:
        _validate_production(settings)
    return settings


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=False,
    )
