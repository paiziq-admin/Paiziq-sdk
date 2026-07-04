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
from stores.decisions import DecisionStore, ReviewStore
from stores.keys import KeyStore
from stores.orgs import OrgStore
from stores.payments import PaymentStore
from stores.policies import PolicyStore

_org_store: Optional[OrgStore] = None
_agent_store: Optional[AgentStore] = None
_key_store: Optional[KeyStore] = None
_payment_store: Optional[PaymentStore] = None
_decision_store: Optional[DecisionStore] = None
_review_store: Optional[ReviewStore] = None
_policy_store: Optional[PolicyStore] = None
_audit_log: Optional[AuditLog] = None


def init_stores(conn: sqlite3.Connection, lock: threading.Lock) -> None:
    global _org_store, _agent_store, _key_store, _payment_store, _audit_log
    global _decision_store, _review_store, _policy_store
    _org_store = OrgStore(conn, lock)
    _agent_store = AgentStore(conn, lock)
    _key_store = KeyStore(conn, lock)
    _payment_store = PaymentStore(conn, lock)
    _decision_store = DecisionStore(conn, lock)
    _review_store = ReviewStore(conn, lock)
    _policy_store = PolicyStore(conn, lock)
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


def get_payment_store() -> PaymentStore:
    assert _payment_store is not None, "init_stores() not called"
    return _payment_store


def get_decision_store() -> DecisionStore:
    assert _decision_store is not None, "init_stores() not called"
    return _decision_store


def get_review_store() -> ReviewStore:
    assert _review_store is not None, "init_stores() not called"
    return _review_store


def get_policy_store() -> PolicyStore:
    assert _policy_store is not None, "init_stores() not called"
    return _policy_store


def get_audit_log() -> AuditLog:
    assert _audit_log is not None, "init_stores() not called"
    return _audit_log
