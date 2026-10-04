"""Durable execution claims, exact spend, and append-only business evidence.

SQLite is the authority for one configured organization/environment. Sharing a
file shares reservations across workers. Telemetry exporters are never used as
an execution database. A submitted or unknown execution keeps its reservation
until provider evidence resolves it, regardless of its age.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Optional, Protocol

from .models import PaymentRequest
from .engine.policy import BudgetTracker


class ExecutionConflict(ValueError):
    """A logical execution ID was reused with different evidence."""


class GatewayDeclined(RuntimeError):
    """The provider explicitly confirms that no side effect occurred."""


@dataclass(frozen=True)
class GatewayOutcome:
    """Trusted lookup response; unknown never permits another charge."""

    status: str  # confirmed | failed | unknown
    gateway_reference: Optional[str] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in {"confirmed", "failed", "unknown"}:
            raise ValueError("Invalid provider outcome")
        if self.status == "confirmed" and not self.gateway_reference:
            raise ValueError("A confirmed outcome requires a provider reference")


class IdempotentPaymentGateway(Protocol):
    name: str

    def charge_idempotent(self, request: PaymentRequest, idempotency_key: str) -> str: ...
    def lookup(self, idempotency_key: str) -> GatewayOutcome: ...


def canonical_json(value: Any) -> str:
    """Stable JSON; unsupported evidence and nonfinite values fail closed."""
    def encode(item: Any) -> Any:
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        if isinstance(item, (set, frozenset)):
            return sorted(item)
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError(f"Unsupported evidence type: {type(item).__name__}")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=encode)


def evidence_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def money_decimal(amount: Any, currency: str) -> Decimal:
    """Validate an exact ISO currency amount without rounding authorized spend."""
    zero = {"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF"}
    three = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
    precision = 0 if currency.upper() in zero else 3 if currency.upper() in three else 4 if currency.upper() in {"CLF", "UYW"} else 2
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or value <= 0:
            raise ValueError("Amount must be positive and finite")
        if value != value.quantize(Decimal(1).scaleb(-precision)):
            raise ValueError(f"Amount exceeds {currency.upper()} minor-unit precision ({precision})")
    except InvalidOperation as exc:
        raise ValueError("Invalid currency amount") from exc
    return value


def request_snapshot(request: PaymentRequest) -> dict[str, Any]:
    data = asdict(request)
    # Receipt time is not part of the requested side effect. All caller payload,
    # including mandate and metadata, is bound without lossy normalization.
    data.pop("created_at_ms", None)
    data["currency"] = request.currency.upper()
    data["amount"] = str(money_decimal(request.amount, request.currency).normalize())
    return json.loads(canonical_json(data))


def request_digest(request: PaymentRequest) -> str:
    return evidence_digest(request_snapshot(request))


def policy_digest(policy: Any, *, version: str = "local", rules: Optional[list[Any]] = None) -> str:
    data: dict[str, Any] = {"policy": policy, "version": version}
    if rules is not None:
        data["rules"] = [{
            "type": f"{type(rule).__module__}.{type(rule).__qualname__}",
            "name": getattr(rule, "name", ""),
            "configuration": {key: val for key, val in vars(rule).items() if key != "tracker"},
        } for rule in rules]
    return evidence_digest(data)


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    request_id: str
    org_id: str
    env_id: str
    agent_id: str
    currency: str
    amount: str
    decision_id: str
    request_digest: str
    policy_digest: str
    context_digest: str
    provider_idempotency_key: str
    status: str
    gateway_reference: Optional[str]
    error: Optional[str]
    created_at_ms: int
    updated_at_ms: int
    confirmed_at_ms: Optional[int]
    request: dict[str, Any]
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClaimResult:
    record: Optional[ExecutionRecord]
    acquired: bool
    reason: Optional[str] = None


class ExecutionLedger(Protocol):
    def get(self, request_id: str, *, org_id: str = "local", env_id: str = "local") -> Optional[ExecutionRecord]: ...
    def claim(self, request: PaymentRequest, *, org_id: str, env_id: str, request_digest: str, policy_digest: str, decision_id: str, context: Optional[dict] = None, daily_budget: Optional[float] = None, monthly_budget: Optional[float] = None, max_tx_per_hour: Optional[int] = None, now_ms: Optional[int] = None) -> ClaimResult: ...
    def transition(self, request_id: str, status: str, *, org_id: str, env_id: str, gateway_reference: Optional[str] = None, error: Optional[str] = None, evidence: Optional[dict] = None, now_ms: Optional[int] = None) -> ExecutionRecord: ...


LEDGER_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS execution_records (
        execution_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
        org_id TEXT NOT NULL, env_id TEXT NOT NULL, agent_id TEXT NOT NULL,
        currency TEXT NOT NULL, amount TEXT NOT NULL, decision_id TEXT NOT NULL,
        request_digest TEXT NOT NULL, policy_digest TEXT NOT NULL,
        context_digest TEXT NOT NULL, provider_idempotency_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL, gateway_reference TEXT, error TEXT,
        created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL,
        confirmed_at_ms INTEGER, request_json TEXT NOT NULL, context_json TEXT NOT NULL,
        UNIQUE(org_id, env_id, request_id))""",
    """CREATE INDEX IF NOT EXISTS execution_budget_scope ON execution_records
        (org_id, env_id, agent_id, currency, status)""",
    """CREATE TABLE IF NOT EXISTS execution_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
        org_id TEXT NOT NULL, env_id TEXT NOT NULL, request_id TEXT NOT NULL,
        event_type TEXT NOT NULL, recorded_at_ms INTEGER NOT NULL, payload_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS execution_outbox_ack (
        event_id TEXT NOT NULL, consumer TEXT NOT NULL, acknowledged_at_ms INTEGER NOT NULL,
        PRIMARY KEY(event_id, consumer))""",
    """CREATE TABLE IF NOT EXISTS execution_reviews (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL,
        env_id TEXT NOT NULL, request_id TEXT NOT NULL, decision_id TEXT NOT NULL,
        request_digest TEXT NOT NULL, policy_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL, UNIQUE(org_id, env_id, decision_id))""",
    """CREATE TABLE IF NOT EXISTS execution_approvals (
        org_id TEXT NOT NULL, env_id TEXT NOT NULL, decision_id TEXT NOT NULL,
        reviewer_id TEXT NOT NULL, approved_at_ms INTEGER NOT NULL,
        PRIMARY KEY(org_id, env_id, decision_id))""",
]

# Business events, review revisions and acknowledgements are immutable even
# when consumers hold a raw connection. Execution state is a mutable projection;
# its authorization identity/evidence is immutable.
for _table in ("execution_events", "execution_reviews", "execution_approvals", "execution_outbox_ack"):
    for _operation in ("UPDATE", "DELETE"):
        LEDGER_SCHEMA.append(
            f"CREATE TRIGGER IF NOT EXISTS {_table}_no_{_operation.lower()} BEFORE {_operation} ON {_table} "
            "BEGIN SELECT RAISE(ABORT, 'execution evidence is append-only'); END"
        )
LEDGER_SCHEMA.append(
    "CREATE TRIGGER IF NOT EXISTS execution_records_evidence_immutable BEFORE UPDATE OF "
    "execution_id,request_id,org_id,env_id,agent_id,currency,amount,decision_id,request_digest,"
    "policy_digest,context_digest,provider_idempotency_key,created_at_ms,request_json,context_json "
    "ON execution_records BEGIN SELECT RAISE(ABORT, 'execution authorization is immutable'); END"
)
LEDGER_SCHEMA.append(
    "CREATE TRIGGER IF NOT EXISTS execution_records_no_delete BEFORE DELETE ON execution_records "
    "BEGIN SELECT RAISE(ABORT, 'execution records cannot be deleted'); END"
)


class SQLiteExecutionLedger:
    """Transactional ledger with immutable events and an acknowledgement outbox.

    ``connection`` and ``lock`` let a host include execution changes in its own
    SQLite transaction. Nested writes use savepoints and never commit the outer
    transaction. A file path is required for restart durability; ``:memory:`` is
    intentionally limited to development and tests.
    """

    def __init__(self, path: str | Path = ":memory:", *, connection: Optional[sqlite3.Connection] = None, lock: Any = None) -> None:
        self._lock = lock or threading.RLock()
        self._owns_connection = connection is None
        self._conn = connection or sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA busy_timeout=30000")
        if self._owns_connection:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
        with self._transaction():
            for statement in LEDGER_SCHEMA:
                self._conn.execute(statement)

    def _query(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        cursor = self._conn.cursor()
        cursor.row_factory = sqlite3.Row
        return cursor.execute(sql, parameters)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            nested = self._conn.in_transaction
            name = "execution_" + uuid.uuid4().hex
            self._conn.execute(f"SAVEPOINT {name}" if nested else "BEGIN IMMEDIATE")
            try:
                yield
                self._conn.execute(f"RELEASE SAVEPOINT {name}" if nested else "COMMIT")
            except BaseException:
                if nested:
                    self._conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
                    self._conn.execute(f"RELEASE SAVEPOINT {name}")
                else:
                    self._conn.rollback()
                raise

    @staticmethod
    def _record(row: sqlite3.Row) -> ExecutionRecord:
        data = dict(row)
        data["request"] = json.loads(data.pop("request_json"))
        data["context"] = json.loads(data.pop("context_json"))
        return ExecutionRecord(**data)

    @staticmethod
    def _now(now_ms: Optional[int]) -> int:
        return now_ms if now_ms is not None else int(time.time() * 1000)

    @staticmethod
    def _scope(org_id: str, env_id: str) -> None:
        if not org_id.strip() or not env_id.strip():
            raise ValueError("Execution organization and environment must be non-empty")

    def get(self, request_id: str, *, org_id: str = "local", env_id: str = "local") -> Optional[ExecutionRecord]:
        with self._lock:
            row = self._query("SELECT * FROM execution_records WHERE org_id=? AND env_id=? AND request_id=?", (org_id, env_id, request_id)).fetchone()
            return self._record(row) if row else None

    def append_event(self, event_type: str, request_id: str, payload: dict, *, org_id: str = "local", env_id: str = "local", now_ms: Optional[int] = None) -> str:
        with self._transaction():
            event_id = "evt_" + uuid.uuid4().hex
            self._conn.execute("INSERT INTO execution_events(event_id,org_id,env_id,request_id,event_type,recorded_at_ms,payload_json) VALUES(?,?,?,?,?,?,?)", (event_id, org_id, env_id, request_id, event_type, self._now(now_ms), canonical_json(payload)))
            return event_id

    def claim(self, request: PaymentRequest, *, org_id: str = "local", env_id: str = "local", request_digest: str, policy_digest: str, decision_id: str, context: Optional[dict] = None, daily_budget: Optional[float] = None, monthly_budget: Optional[float] = None, max_tx_per_hour: Optional[int] = None, now_ms: Optional[int] = None) -> ClaimResult:
        self._scope(org_id, env_id)
        snapshot = request_snapshot(request)
        if evidence_digest(snapshot) != request_digest:
            raise ExecutionConflict("Request digest does not match the payment payload")
        now = self._now(now_ms)
        amount = money_decimal(request.amount, request.currency)
        context = json.loads(canonical_json(context or {}))
        with self._transaction():
            previous = self.get(request.request_id, org_id=org_id, env_id=env_id)
            if previous:
                if previous.request_digest != request_digest:
                    raise ExecutionConflict("Logical action already exists with a different request digest")
                return ClaimResult(previous, False, "execution_already_claimed")
            for label, budget, window in (("daily", daily_budget, 86_400_000), ("monthly", monthly_budget, 30 * 86_400_000)):
                if budget is not None:
                    spent = self.spend_since(request.agent_id, now - window, currency=request.currency, org_id=org_id, env_id=env_id)
                    spent += Decimal(str(context.get("legacy_budget_history", {}).get(label + "_spend", 0)))
                    limit = money_decimal(budget, request.currency)
                    projected = spent + amount
                    if projected > limit:
                        return ClaimResult(None, False, f"{label}_budget_exceeded")
                    ratio = context.get("budget_warning_ratio")
                    if ratio is not None and not context.get("ignore_budget_warnings", False):
                        authorized = Decimal(str(context.get("approved_budget_exposure", {}).get(label, 0)))
                        if projected > limit * Decimal(str(ratio)) and projected > authorized:
                            return ClaimResult(None, False, f"{label}_budget_review_required")
            if max_tx_per_hour is not None and (self.tx_count_since(request.agent_id, now - 3_600_000, currency=request.currency, org_id=org_id, env_id=env_id) + context.get("legacy_budget_history", {}).get("hourly_tx_count", 0)) >= max_tx_per_hour:
                return ClaimResult(None, False, "velocity_limit_exceeded")
            execution_id = "exe_" + uuid.uuid4().hex
            key = "pz_" + evidence_digest([org_id, env_id, request.request_id])
            self._conn.execute("""INSERT INTO execution_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                execution_id, request.request_id, org_id, env_id, request.agent_id, request.currency.upper(), str(amount), decision_id,
                request_digest, policy_digest, evidence_digest(context), key, "reserved", None, None, now, now, None, canonical_json(snapshot), canonical_json(context)))
            record = self.get(request.request_id, org_id=org_id, env_id=env_id)
            assert record is not None
            self.append_event("execution_reserved", request.request_id, record.to_dict(), org_id=org_id, env_id=env_id, now_ms=now)
            return ClaimResult(record, True)

    def transition(self, request_id: str, status: str, *, org_id: str = "local", env_id: str = "local", gateway_reference: Optional[str] = None, error: Optional[str] = None, evidence: Optional[dict] = None, now_ms: Optional[int] = None) -> ExecutionRecord:
        allowed = {"reserved": {"submitted", "failed", "unknown"}, "submitted": {"confirmed", "failed", "unknown"}, "unknown": {"confirmed", "failed", "unknown"}, "confirmed": set(), "failed": set()}
        with self._transaction():
            previous = self.get(request_id, org_id=org_id, env_id=env_id)
            if previous is None:
                raise KeyError(request_id)
            if status == previous.status:
                if gateway_reference and gateway_reference != previous.gateway_reference:
                    raise ExecutionConflict("Conflicting provider reference")
                return previous
            if status not in allowed.get(previous.status, set()):
                raise ExecutionConflict(f"Invalid execution transition {previous.status} -> {status}")
            if status == "confirmed" and not gateway_reference:
                raise ValueError("Confirmed execution requires a provider reference")
            if status in {"confirmed", "failed"} and previous.status == "unknown" and not evidence:
                raise ValueError("Unknown execution requires reconciliation evidence")
            now = self._now(now_ms)
            self._conn.execute("UPDATE execution_records SET status=?,gateway_reference=?,error=?,updated_at_ms=?,confirmed_at_ms=? WHERE execution_id=?", (status, gateway_reference, error, now, now if status == "confirmed" else None, previous.execution_id))
            record = self.get(request_id, org_id=org_id, env_id=env_id)
            assert record is not None
            self.append_event("execution_" + status, request_id, {"record": record.to_dict(), "evidence": evidence or {}}, org_id=org_id, env_id=env_id, now_ms=now)
            return record

    def spend_since(self, agent_id: str, since_ms: int, *, currency: str = "USD", org_id: str = "local", env_id: str = "local", include_reserved: bool = True) -> Decimal:
        with self._lock:
            rows = self._query("SELECT amount,status,confirmed_at_ms FROM execution_records WHERE org_id=? AND env_id=? AND agent_id=? AND currency=?", (org_id, env_id, agent_id, currency.upper())).fetchall()
            return sum((Decimal(row["amount"]) for row in rows if (row["status"] == "confirmed" and row["confirmed_at_ms"] >= since_ms) or (include_reserved and row["status"] in {"reserved", "submitted", "unknown"})), Decimal(0))

    def tx_count_since(self, agent_id: str, since_ms: int, *, currency: str = "USD", org_id: str = "local", env_id: str = "local", include_reserved: bool = True) -> int:
        with self._lock:
            rows = self._query("SELECT status,confirmed_at_ms FROM execution_records WHERE org_id=? AND env_id=? AND agent_id=? AND currency=?", (org_id, env_id, agent_id, currency.upper())).fetchall()
            return sum(1 for row in rows if (row["status"] == "confirmed" and row["confirmed_at_ms"] >= since_ms) or (include_reserved and row["status"] in {"reserved", "submitted", "unknown"}))

    def list_events(self, request_id: Optional[str] = None, *, org_id: str = "local", env_id: str = "local", limit: int = 100) -> list[dict]:
        with self._lock:
            sql = "SELECT * FROM execution_events WHERE org_id=? AND env_id=?"
            args: list[Any] = [org_id, env_id]
            if request_id is not None:
                sql += " AND request_id=?"
                args.append(request_id)
            rows = self._query(sql + " ORDER BY sequence DESC LIMIT ?", (*args, limit)).fetchall()
            return [self._event(row) for row in reversed(rows)]

    @staticmethod
    def _event(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        return data

    def pending_events(self, *, org_id: str = "local", env_id: str = "local", limit: int = 100, consumer: str = "default") -> list[dict]:
        with self._lock:
            rows = self._query("""SELECT e.* FROM execution_events e WHERE org_id=? AND env_id=?
                AND NOT EXISTS(SELECT 1 FROM execution_outbox_ack a WHERE a.event_id=e.event_id AND a.consumer=?)
                ORDER BY sequence LIMIT ?""", (org_id, env_id, consumer, limit)).fetchall()
            return [self._event(row) for row in rows]

    def acknowledge(self, event_id: str, *, org_id: str = "local", env_id: str = "local", consumer: str = "default") -> None:
        with self._transaction():
            exists = self._query("SELECT 1 FROM execution_events WHERE event_id=? AND org_id=? AND env_id=?", (event_id, org_id, env_id)).fetchone()
            if not exists:
                raise KeyError(event_id)
            self._conn.execute("INSERT OR IGNORE INTO execution_outbox_ack VALUES(?,?,?)", (event_id, consumer, self._now(None)))

    def record_review(self, request_id: str, decision_id: str, request_digest: str, policy_digest: str, payload: dict, *, org_id: str = "local", env_id: str = "local") -> None:
        with self._transaction():
            self._conn.execute("INSERT INTO execution_reviews(org_id,env_id,request_id,decision_id,request_digest,policy_digest,payload_json) VALUES(?,?,?,?,?,?,?)", (org_id, env_id, request_id, decision_id, request_digest, policy_digest, canonical_json(payload)))
            self.append_event("authorization_reviewed", request_id, payload | {"request_digest": request_digest, "policy_digest": policy_digest}, org_id=org_id, env_id=env_id)

    def latest_review(self, request_id: str, *, org_id: str = "local", env_id: str = "local") -> Optional[dict]:
        with self._lock:
            row = self._query("SELECT * FROM execution_reviews WHERE org_id=? AND env_id=? AND request_id=? ORDER BY sequence DESC LIMIT 1", (org_id, env_id, request_id)).fetchone()
            if not row:
                return None
            return dict(row) | {"payload": json.loads(row["payload_json"])}

    def approve_review(self, request_id: str, decision_id: str, reviewer_id: str, *, org_id: str = "local", env_id: str = "local") -> None:
        if not reviewer_id.strip():
            raise ValueError("A reviewer identity is required")
        with self._transaction():
            row = self.latest_review(request_id, org_id=org_id, env_id=env_id)
            if row is None or row["decision_id"] != decision_id:
                raise ExecutionConflict("Review is not the current request revision")
            self._conn.execute("INSERT OR IGNORE INTO execution_approvals VALUES(?,?,?,?,?)", (org_id, env_id, decision_id, reviewer_id, self._now(None)))
            self.append_event("authorization_approved", request_id, {"decision_id": decision_id, "reviewer_id": reviewer_id, "request_digest": row["request_digest"], "policy_digest": row["policy_digest"]}, org_id=org_id, env_id=env_id)

    def review_approved(self, decision_id: str, *, org_id: str = "local", env_id: str = "local") -> bool:
        with self._lock:
            return self._query("SELECT 1 FROM execution_approvals WHERE org_id=? AND env_id=? AND decision_id=?", (org_id, env_id, decision_id)).fetchone() is not None

    def import_confirmed(self, request: PaymentRequest, *, org_id: str = "local", env_id: str = "local", reference: str, now_ms: Optional[int] = None, provenance: str = "legacy_external_report", allow_legacy_precision: bool = False) -> ExecutionRecord:
        """Import observed historical spend; this does not grant authorization.

        Old records may contain fractional minor units. An explicit migration
        flag preserves that exact amount and labels it historical evidence.
        New claims always enforce currency precision. No old spend is rounded.
        """
        self._scope(org_id, env_id)
        if not reference:
            raise ValueError("Historical execution needs an external record reference")
        if allow_legacy_precision:
            amount = Decimal(str(request.amount))
            if not amount.is_finite() or amount <= 0:
                raise ValueError("Historical amount must be positive and finite")
            snapshot = asdict(request)
            snapshot.pop("created_at_ms", None)
            snapshot["currency"] = request.currency.upper()
            snapshot["amount"] = str(amount.normalize())
            snapshot = json.loads(canonical_json(snapshot))
        else:
            snapshot = request_snapshot(request)
            amount = money_decimal(request.amount, request.currency)
        digest = evidence_digest(snapshot)
        context = {"provenance": provenance, "authorization": "historical_external_report", "legacy_precision": allow_legacy_precision}
        now = self._now(now_ms)
        with self._transaction():
            previous = self.get(request.request_id, org_id=org_id, env_id=env_id)
            if previous:
                if previous.request_digest != digest:
                    raise ExecutionConflict("Historical action has a different request digest")
                return previous
            execution_id = "exe_" + uuid.uuid4().hex
            key = "pz_" + evidence_digest([org_id, env_id, request.request_id])
            self._conn.execute("INSERT INTO execution_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                execution_id, request.request_id, org_id, env_id, request.agent_id, request.currency.upper(), str(amount), "legacy",
                digest, "legacy", evidence_digest(context), key, "confirmed", reference, None, now, now, now,
                canonical_json(snapshot), canonical_json(context)))
            record = self.get(request.request_id, org_id=org_id, env_id=env_id)
            assert record is not None
            self.append_event("execution_confirmed", request.request_id, {"record": record.to_dict(), "evidence": context}, org_id=org_id, env_id=env_id, now_ms=now)
            return record

    def close(self) -> None:
        if self._owns_connection:
            with self._lock:
                self._conn.close()


class LedgerBudgetStore:
    """BudgetStore compatibility adapter, pinned to one currency and scope."""

    def __init__(self, ledger: SQLiteExecutionLedger, *, org_id: str = "local", env_id: str = "local", currency: str = "USD") -> None:
        self.ledger, self.org_id, self.env_id, self.currency = ledger, org_id, env_id, currency

    def spend_since(self, agent_id: str, since_ts: float) -> float:
        return float(self.ledger.spend_since(agent_id, int(since_ts * 1000), currency=self.currency, org_id=self.org_id, env_id=self.env_id))

    def tx_count_since(self, agent_id: str, since_ts: float) -> int:
        return self.ledger.tx_count_since(agent_id, int(since_ts * 1000), currency=self.currency, org_id=self.org_id, env_id=self.env_id)

    def record_spend(self, agent_id: str, amount: float, ts: Optional[float] = None) -> None:
        request = PaymentRequest(agent_id=agent_id, principal_id="legacy-budget-store", merchant="legacy-budget-store", amount=amount, currency=self.currency)
        self.ledger.import_confirmed(request, org_id=self.org_id, env_id=self.env_id, reference="legacy_budget_record", now_ms=int(ts * 1000) if ts is not None else None)


class LedgerBudgetTracker(BudgetTracker):
    """Currency-aware BudgetTracker surface over the authoritative ledger.

    An old injected tracker is an additional USD history source. New executions
    are recorded only in the ledger, so they are never counted twice. Its
    history must not be independently written with these same executions.
    """

    def __init__(self, ledger: SQLiteExecutionLedger, *, org_id: str = "local", env_id: str = "local", legacy: Any = None, clock: Any = None) -> None:
        self.ledger, self.org_id, self.env_id = ledger, org_id, env_id
        self.legacy = legacy
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.store = LedgerBudgetStore(ledger, org_id=org_id, env_id=env_id)

    def _spend(self, agent_id: str, currency: str, window: int, legacy_method: str) -> float:
        if self.legacy and currency != "USD":
            raise ValueError("Legacy BudgetTracker history has no currency; only USD is supported")
        total = self.ledger.spend_since(agent_id, self.clock() - window, org_id=self.org_id, env_id=self.env_id, currency=currency)
        if self.legacy:
            total += Decimal(str(getattr(self.legacy, legacy_method)(agent_id)))
        return float(total)

    def daily_spend(self, agent_id: str, currency: str = "USD") -> float:
        return self._spend(agent_id, currency, 86_400_000, "daily_spend")

    def monthly_spend(self, agent_id: str, currency: str = "USD") -> float:
        return self._spend(agent_id, currency, 30 * 86_400_000, "monthly_spend")

    def hourly_tx_count(self, agent_id: str, currency: str = "USD") -> int:
        if self.legacy and currency != "USD":
            raise ValueError("Legacy BudgetTracker history has no currency; only USD is supported")
        total = self.ledger.tx_count_since(agent_id, self.clock() - 3_600_000, org_id=self.org_id, env_id=self.env_id, currency=currency)
        return total + (self.legacy.hourly_tx_count(agent_id) if self.legacy else 0)

    def commit(self, agent_id: str, amount: float) -> None:
        self.store.record_spend(agent_id, amount)
