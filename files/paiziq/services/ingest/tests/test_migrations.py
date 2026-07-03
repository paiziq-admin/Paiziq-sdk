"""Migration runner tests: ordering, idempotency, legacy adoption,
domain-table constraints, and append-only audit enforcement."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from migrations import (  # noqa: E402
    MigrationError,
    apply_migrations,
    applied_versions,
    discover_migrations,
)
from storage import IngestStore  # noqa: E402

EXPECTED_TABLES = {
    "spans", "notifications", "organizations", "environments", "agents",
    "api_keys", "payments", "payment_transitions", "decisions", "reviews",
    "policies", "policy_versions", "audit_log", "schema_migrations",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows if not r[0].startswith("sqlite_")}


def _seed_org_env(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO organizations (id, name, created_at_ms) VALUES ('org_1', 'acme', 1)"
    )
    conn.execute(
        "INSERT INTO environments (id, org_id, name, kind, created_at_ms) "
        "VALUES ('env_1', 'org_1', 'sandbox', 'sandbox', 1)"
    )


def test_fresh_database_gets_all_tables():
    conn = sqlite3.connect(":memory:")
    applied = apply_migrations(conn)
    assert applied == [p.name for _, p in discover_migrations()]
    assert EXPECTED_TABLES <= _tables(conn)


def test_migrations_are_idempotent():
    conn = sqlite3.connect(":memory:")
    first = apply_migrations(conn)
    assert first  # something ran
    assert apply_migrations(conn) == []  # nothing pending on re-run
    assert applied_versions(conn) == {v for v, _ in discover_migrations()}


def test_legacy_pre_migration_database_adopts_cleanly(tmp_path):
    """A DB created by the old inline schema migrates without error."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE spans (span_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, "
                 "name TEXT NOT NULL, parent_span_id TEXT, start_ms INTEGER, end_ms INTEGER, "
                 "status TEXT, payload TEXT NOT NULL)")
    conn.execute("INSERT INTO spans VALUES ('s1', 'tr1', 'n', NULL, 1, 2, 'ok', '{}')")
    conn.commit()
    conn.close()
    store = IngestStore(str(db))
    assert store.span_count() == 1  # legacy data survives adoption


def test_bad_migration_filename_rejected(tmp_path):
    (tmp_path / "1_bad.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="Invalid migration filename"):
        discover_migrations(tmp_path)


def test_failed_migration_rolls_back(tmp_path):
    (tmp_path / "0001_boom.sql").write_text("CREATE TABLE ok (id TEXT); INVALID SQL;")
    conn = sqlite3.connect(":memory:")
    with pytest.raises(MigrationError, match="0001_boom.sql"):
        apply_migrations(conn, tmp_path)
    assert applied_versions(conn) == set()


def test_domain_constraints_enforced():
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    _seed_org_env(conn)
    with pytest.raises(sqlite3.IntegrityError):  # duplicate org name
        conn.execute(
            "INSERT INTO organizations (id, name, created_at_ms) VALUES ('org_2', 'acme', 1)"
        )
    with pytest.raises(sqlite3.IntegrityError):  # invalid environment kind
        conn.execute(
            "INSERT INTO environments (id, org_id, name, kind, created_at_ms) "
            "VALUES ('env_2', 'org_1', 'prod', 'staging', 1)"
        )
    with pytest.raises(sqlite3.IntegrityError):  # FK: unknown environment
        conn.execute(
            "INSERT INTO agents (id, env_id, name, created_at_ms) "
            "VALUES ('agt_1', 'env_missing', 'a', 1)"
        )


def test_payment_state_check_constraint():
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    _seed_org_env(conn)
    conn.execute(
        "INSERT INTO agents (id, env_id, name, created_at_ms) VALUES ('agt_1', 'env_1', 'a', 1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO payments (id, env_id, agent_id, principal_id, merchant, amount, "
            "state, created_at_ms, updated_at_ms) "
            "VALUES ('pay_1', 'env_1', 'agt_1', 'u1', 'm', 5.0, 'teleported', 1, 1)"
        )


def test_audit_log_is_append_only():
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    conn.execute(
        "INSERT INTO audit_log (audit_id, actor, action, resource, at_ms) "
        "VALUES ('aud_1', 'key_1', 'payment.transition', 'pay_1', 1)"
    )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit_log SET actor = 'evil' WHERE audit_id = 'aud_1'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit_log WHERE audit_id = 'aud_1'")
