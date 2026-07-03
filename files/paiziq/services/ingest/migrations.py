"""Versioned SQLite schema migrations for the ingest service.

Migrations are numbered SQL files in migrations/ (e.g.
`0001_baseline_ingest.sql`), applied in order exactly once and recorded
in `schema_migrations`. The runner is stdlib-only and transactional per
migration: a failing migration rolls back and aborts startup.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version       INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    applied_at_ms INTEGER NOT NULL
)
"""


class MigrationError(RuntimeError):
    """Raised when the migrations directory or a migration is invalid."""


def discover_migrations(directory: Path | None = None) -> list[tuple[int, Path]]:
    """Return (version, path) pairs sorted by version; validate names."""
    directory = directory or MIGRATIONS_DIR
    found: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if not match:
            raise MigrationError(f"Invalid migration filename: {path.name}")
        found.append((int(match.group(1)), path))
    versions = [v for v, _ in found]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"Duplicate migration versions in {directory}")
    return found


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(_TRACKING_TABLE)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def apply_migrations(
    conn: sqlite3.Connection, directory: Path | None = None
) -> list[str]:
    """Apply pending migrations in order; return the names applied."""
    conn.execute("PRAGMA foreign_keys = ON")
    done = applied_versions(conn)
    applied: list[str] = []
    for version, path in discover_migrations(directory):
        if version in done:
            continue
        try:
            conn.executescript(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at_ms) VALUES (?, ?, ?)",
                (version, path.name, int(time.time() * 1000)),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise MigrationError(f"Migration {path.name} failed: {exc}") from exc
        applied.append(path.name)
    return applied
