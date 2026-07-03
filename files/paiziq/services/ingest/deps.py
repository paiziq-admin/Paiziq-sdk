"""FastAPI dependency wiring for control-plane stores.

app.py calls init_stores() once at startup with the shared SQLite
connection; routers resolve stores through the get_* dependencies so
tests can rebuild state by re-initializing.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Optional

from audit import AuditLog
from stores.agents import AgentStore
from stores.keys import KeyStore
from stores.orgs import OrgStore

_org_store: Optional[OrgStore] = None
_agent_store: Optional[AgentStore] = None
_key_store: Optional[KeyStore] = None
_audit_log: Optional[AuditLog] = None


def init_stores(conn: sqlite3.Connection, lock: threading.Lock) -> None:
    global _org_store, _agent_store, _key_store, _audit_log
    _org_store = OrgStore(conn, lock)
    _agent_store = AgentStore(conn, lock)
    _key_store = KeyStore(conn, lock)
    _audit_log = AuditLog(conn, lock)


def get_org_store() -> OrgStore:
    assert _org_store is not None, "init_stores() not called"
    return _org_store


def get_agent_store() -> AgentStore:
    assert _agent_store is not None, "init_stores() not called"
    return _agent_store


def get_key_store() -> KeyStore:
    assert _key_store is not None, "init_stores() not called"
    return _key_store


def get_audit_log() -> AuditLog:
    assert _audit_log is not None, "init_stores() not called"
    return _audit_log
