"""Configuration tests: development defaults, production fail-fast
validation, and env parsing edge cases."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DEV_KEY, ConfigError, Settings, load_settings  # noqa: E402

PROD_ENV = {
    "PAIZIQ_ENV": "production",
    "PAIZIQ_INGEST_DB": "/var/lib/paiziq/ingest.db",
    "PAIZIQ_INGEST_KEYS": "pzq_prod_alpha,pzq_prod_beta",
}


def test_development_defaults():
    s = load_settings({})
    assert s.environment == "development"
    assert not s.is_production
    assert s.database_path == ":memory:"
    assert s.api_keys == frozenset({DEV_KEY})
    assert s.max_body_bytes == 1_000_000
    assert s.max_spans_per_batch == 500


def test_production_valid_config_loads():
    s = load_settings(PROD_ENV)
    assert s.is_production
    assert s.api_keys == frozenset({"pzq_prod_alpha", "pzq_prod_beta"})
    assert s.database_path == "/var/lib/paiziq/ingest.db"


def test_production_rejects_memory_db():
    env = {**PROD_ENV, "PAIZIQ_INGEST_DB": ":memory:"}
    with pytest.raises(ConfigError, match="PAIZIQ_INGEST_DB"):
        load_settings(env)


def test_production_rejects_dev_key():
    env = {**PROD_ENV, "PAIZIQ_INGEST_KEYS": f"pzq_prod_alpha,{DEV_KEY}"}
    with pytest.raises(ConfigError, match="development key"):
        load_settings(env)


def test_production_rejects_empty_keys():
    env = {**PROD_ENV, "PAIZIQ_INGEST_KEYS": " , "}
    with pytest.raises(ConfigError, match="at least one API key"):
        load_settings(env)


def test_unknown_environment_rejected():
    with pytest.raises(ConfigError, match="PAIZIQ_ENV"):
        load_settings({"PAIZIQ_ENV": "staging"})


def test_limit_overrides_and_validation():
    s = load_settings({"PAIZIQ_MAX_BODY_BYTES": "2000000", "PAIZIQ_MAX_SPANS_PER_BATCH": "50"})
    assert (s.max_body_bytes, s.max_spans_per_batch) == (2_000_000, 50)
    with pytest.raises(ConfigError, match="must be an integer"):
        load_settings({"PAIZIQ_MAX_BODY_BYTES": "lots"})
    with pytest.raises(ConfigError, match="must be positive"):
        load_settings({"PAIZIQ_MAX_SPANS_PER_BATCH": "0"})


def test_invalid_log_level_rejected():
    with pytest.raises(ConfigError, match="PAIZIQ_LOG_LEVEL"):
        load_settings({"PAIZIQ_LOG_LEVEL": "SHOUTING"})


def test_settings_are_immutable():
    s = Settings()
    with pytest.raises(Exception):
        s.environment = "production"  # type: ignore[misc]
