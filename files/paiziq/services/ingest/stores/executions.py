"""Hosted authority over the SDK ledger and immutable authorization bindings."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager

from paiziq import Mandate, PaymentRequest
from paiziq.engine import DecisionEngine
from paiziq.engine.audit4 import FourWayAuditor, transaction_snapshot
from paiziq.engine.policy import BudgetTracker
from paiziq.execution import (
    SQLiteExecutionLedger,
    canonical_json,
    policy_digest,
    request_digest,
    request_snapshot,
)
from paiziq.models import DecisionStatus

from envelope import ApiError
from ids import new_id, now_ms
from policy_doc import from_policy, to_policy
from stores.payments import PaymentStore
from stores.policies import PolicyStore


class ExecutionStore:
    def __init__(self, conn, lock) -> None:
        self.conn, self.lock = conn, lock
        self.ledger = SQLiteExecutionLedger(connection=conn, lock=lock)
        self.payments = PaymentStore(conn, lock)
        self.policies = PolicyStore(conn, lock)
        self._import_legacy_history()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def scope(self, payment):
        row = self.conn.execute(
            "SELECT org_id FROM environments WHERE id=?", (payment["env_id"],)
        ).fetchone()
        return {"org_id": row[0], "env_id": payment["env_id"]}

    @staticmethod
    def request(payment, *, legacy=False):
        return PaymentRequest(
            agent_id=payment["agent_id"],
            principal_id=payment["principal_id"],
            merchant=payment["merchant"],
            amount=payment["amount"],
            currency=payment["currency"],
            intent_description=payment["intent_description"],
            category=payment.get("category", "general"),
            mandate=Mandate(**payment["mandate"]) if payment.get("mandate") else None,
            metadata=payment.get("metadata", {}),
            request_id=payment["id"]
            if legacy
            else (payment.get("request_id") or payment["id"]),
            created_at_ms=payment["created_at_ms"],
        )

    def _import_legacy_history(self):
        with self.lock:
            ids = self.conn.execute(
                "SELECT id FROM payments WHERE state='executed' AND id NOT IN (SELECT payment_id FROM execution_bindings)"
            ).fetchall()
            for row in ids:
                payment = self.payments.get(row[0])
                self.ledger.import_confirmed(
                    self.request(payment, legacy=True),
                    **self.scope(payment),
                    reference="legacy_external_report",
                    now_ms=payment["updated_at_ms"],
                    allow_legacy_precision=True,
                )

    def evaluate(self, payment, active=None):
        active = (
            active
            if active is not None
            else self.policies.active_for_env(payment["env_id"])
        )
        policy = to_policy(active["document"] if active else {})
        digest = policy_digest(
            policy,
            version=f"{active['policy_id']}:{active['version']}"
            if active
            else "default",
        )
        request = self.request(payment)
        with self.lock:
            timestamp = now_ms()
            scope = self.scope(payment)
            args = {**scope, "currency": request.currency}
            history = {
                "evaluated_at_ms": timestamp,
                "scope": {
                    **scope,
                    "agent_id": request.agent_id,
                    "currency": request.currency,
                },
                "ledger_sequence": self.conn.execute(
                    "SELECT COALESCE(MAX(sequence),0) FROM execution_events WHERE org_id=? AND env_id=?",
                    (scope["org_id"], scope["env_id"]),
                ).fetchone()[0],
                "daily_exposure": str(
                    self.ledger.spend_since(
                        request.agent_id, timestamp - 86_400_000, **args
                    )
                ),
                "monthly_exposure": str(
                    self.ledger.spend_since(
                        request.agent_id, timestamp - 30 * 86_400_000, **args
                    )
                ),
                "hourly_tx_count": self.ledger.tx_count_since(
                    request.agent_id, timestamp - 3_600_000, **args
                ),
            }
            tracker = BudgetTracker()
            tracker.daily_spend = lambda _agent, _currency="USD": float(
                history["daily_exposure"]
            )
            tracker.monthly_spend = lambda _agent, _currency="USD": float(
                history["monthly_exposure"]
            )
            tracker.hourly_tx_count = lambda _agent, _currency="USD": history[
                "hourly_tx_count"
            ]
            decision = DecisionEngine(policy=policy, budget_tracker=tracker).evaluate(
                request
            )
            decision.evaluation_context = history
        return request, policy, digest, decision, active

    def save_decision_context(
        self, decision_id, request, policy, digest, payment, context=None
    ):
        self.conn.execute(
            "INSERT INTO decision_contexts VALUES(?,?,?,?,?,?,?)",
            (
                decision_id,
                request_digest(request),
                digest,
                canonical_json(request_snapshot(request)),
                canonical_json(from_policy(policy)),
                now_ms(),
                canonical_json(context or {}),
            ),
        )
        self.ledger.record_review(
            request.request_id,
            decision_id,
            request_digest(request),
            digest,
            {"payment_id": payment["id"], "policy": from_policy(policy)},
            **self.scope(payment),
        )

    def _payment_transition(self, payment, state, actor, reason):
        if payment["state"] == state:
            return
        ts = now_ms()
        self.conn.execute(
            "UPDATE payments SET state=?,updated_at_ms=? WHERE id=?",
            (state, ts, payment["id"]),
        )
        self.conn.execute(
            "INSERT INTO payment_transitions(payment_id,from_state,to_state,actor,reason,at_ms) VALUES(?,?,?,?,?,?)",
            (payment["id"], payment["state"], state, actor, reason, ts),
        )

    def _human_approval(self, payment, request_hash, policy_hash, decision):
        row = self.conn.execute(
            "SELECT d.reasons,d.risk_flags FROM reviews r JOIN decision_contexts c ON c.decision_id=r.decision_id "
            "JOIN decisions d ON d.id=r.decision_id "
            "WHERE r.payment_id=? AND r.state='approved' AND c.request_digest=? AND c.policy_digest=? "
            "AND r.resolved_at_ms > ? ORDER BY r.resolved_at_ms DESC LIMIT 1",
            (payment["id"], request_hash, policy_hash, now_ms() - 900_000),
        ).fetchone()
        return (
            row is not None
            and set(decision.reasons).issubset(json.loads(row[0]))
            and {f.value for f in decision.risk_flags}.issubset(json.loads(row[1]))
        )

    def _deny(self, payment, request, policy, digest, decision, active, actor):
        did, timestamp = new_id("dec"), now_ms()
        self.conn.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
            (
                did,
                payment["id"],
                active["version"] if active else None,
                decision.status.value,
                json.dumps(decision.reasons),
                json.dumps([f.value for f in decision.risk_flags]),
                timestamp,
            ),
        )
        self.save_decision_context(
            did, request, policy, digest, payment, decision.evaluation_context
        )
        self._payment_transition(
            payment, decision.status.value, actor, f"execution denied by {did}"
        )
        if decision.status is DecisionStatus.NEEDS_REVIEW:
            current = self.conn.execute(
                "SELECT id FROM reviews WHERE payment_id=? AND state='open'",
                (payment["id"],),
            ).fetchone()
            if current:
                self.conn.execute(
                    "UPDATE reviews SET decision_id=?,updated_at_ms=? WHERE id=?",
                    (did, timestamp, current[0]),
                )
            else:
                self.conn.execute(
                    "INSERT INTO reviews(id,payment_id,decision_id,state,created_at_ms,updated_at_ms) VALUES(?,?,?,'open',?,?)",
                    (new_id("rev"), payment["id"], did, timestamp, timestamp),
                )
        self.ledger.append_event(
            "execution_denied",
            request.request_id,
            {
                "payment_id": payment["id"],
                "actor": actor,
                "decision_id": did,
                "status": decision.status.value,
                "reasons": decision.reasons,
            },
            **self.scope(payment),
        )
        return {
            "record": None,
            "acquired": False,
            "reason": "; ".join(decision.reasons),
            "claim_token": None,
        }

    def claim(self, payment_id, actor, expected_digest=None):
        with self.transaction():
            payment = self.payments.get(payment_id)
            request = self.request(payment)
            scope = self.scope(payment)
            digest = request_digest(request)
            if expected_digest and expected_digest != digest:
                raise ApiError(
                    409,
                    "request_digest_conflict",
                    "Request does not match the persisted payment",
                )
            previous = self.ledger.get(request.request_id, **scope)
            if previous:
                if previous.request_digest != digest:
                    raise ApiError(
                        409,
                        "request_digest_conflict",
                        "Logical request has a different payload",
                    )
                return {
                    "record": previous.to_dict(),
                    "acquired": False,
                    "reason": "execution_already_claimed",
                    "claim_token": None,
                }
            if payment["state"] not in {"proposed", "approved"}:
                raise ApiError(
                    409,
                    "execution_not_authorized",
                    "Payment must be approved before execution",
                )
            request, policy, pdigest, decision, active = self.evaluate(payment)
            if decision.status is DecisionStatus.NEEDS_REVIEW and self._human_approval(
                payment, digest, pdigest, decision
            ):
                decision.status = DecisionStatus.APPROVED
            if not decision.approved:
                return self._deny(
                    payment, request, policy, pdigest, decision, active, actor
                )
            audited = FourWayAuditor().run(
                request, decision, transaction_snapshot(request)
            )
            if not audited.passed:
                decision.status = DecisionStatus.REJECTED
                decision.reasons = [c.detail for c in audited.checks if not c.passed]
                return self._deny(
                    payment, request, policy, pdigest, decision, active, actor
                )
            did = new_id("dec")
            claim = self.ledger.claim(
                request,
                **scope,
                request_digest=digest,
                policy_digest=pdigest,
                decision_id=did,
                context={
                    **decision.evaluation_context,
                    "payment_id": payment_id,
                    "policy_version": active["version"] if active else None,
                    "policy": from_policy(policy),
                    "evaluated_at_ms": now_ms(),
                    "authorization_expires_at_ms": now_ms() + 900_000,
                    "actor": actor,
                    "four_way_audit": [
                        {
                            "dimension": c.dimension.value,
                            "passed": c.passed,
                            "detail": c.detail,
                        }
                        for c in audited.checks
                    ],
                },
                daily_budget=policy.daily_budget,
                monthly_budget=policy.monthly_budget,
                max_tx_per_hour=policy.max_tx_per_hour,
            )
            if not claim.acquired:
                return {
                    "record": claim.record.to_dict() if claim.record else None,
                    "acquired": False,
                    "reason": claim.reason,
                    "claim_token": None,
                }
            self.conn.execute(
                "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
                (
                    did,
                    payment_id,
                    active["version"] if active else None,
                    "approved",
                    json.dumps(decision.reasons),
                    json.dumps([f.value for f in decision.risk_flags]),
                    now_ms(),
                ),
            )
            self.save_decision_context(
                did, request, policy, pdigest, payment, decision.evaluation_context
            )
            token = secrets.token_urlsafe(32)
            self.conn.execute(
                "INSERT INTO execution_bindings VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    payment_id,
                    scope["org_id"],
                    scope["env_id"],
                    request.request_id,
                    hashlib.sha256(token.encode()).hexdigest(),
                    actor,
                    active["version"] if active else None,
                    canonical_json(request_snapshot(request)),
                    canonical_json(from_policy(policy)),
                    now_ms(),
                ),
            )
            self._payment_transition(
                payment, "approved", actor, f"execution authorization {did}"
            )
            return {
                "record": claim.record.to_dict(),
                "acquired": True,
                "reason": None,
                "claim_token": token,
            }

    def report(
        self,
        payment_id,
        actor,
        status,
        *,
        token=None,
        reference=None,
        error=None,
        evidence=None,
        reconcile=False,
    ):
        with self.transaction():
            payment = self.payments.get(payment_id)
            binding = self.conn.execute(
                "SELECT request_id,claim_token_hash,actor FROM execution_bindings WHERE payment_id=?",
                (payment_id,),
            ).fetchone()
            if binding is None:
                raise ApiError(
                    409,
                    "execution_not_claimed",
                    "Payment has no managed execution claim",
                )
            if not reconcile and (
                binding[2] != actor
                or not token
                or not hmac.compare_digest(
                    binding[1], hashlib.sha256(token.encode()).hexdigest()
                )
            ):
                raise ApiError(
                    403,
                    "invalid_execution_claim",
                    "Execution claim belongs to another executor or token",
                )
            if status in {"confirmed", "failed"} and not evidence:
                raise ApiError(
                    422,
                    "evidence_required",
                    "Provider evidence is required to settle an execution",
                )
            scope = self.scope(payment)
            current = self.ledger.get(binding[0], **scope)
            if status == "submitted" and current.status == "reserved":
                _, _, current_policy_digest, _, _ = self.evaluate(payment)
                if current_policy_digest != current.policy_digest:
                    raise ApiError(
                        409,
                        "policy_revision_changed",
                        "The execution policy changed; cancel this unused reservation and review a new action",
                    )
                if now_ms() >= current.context.get("authorization_expires_at_ms", 0):
                    raise ApiError(
                        409,
                        "authorization_expired",
                        "The execution authorization expired; reconcile the unused reservation",
                    )
                request = self.request(payment)
                from paiziq.models import Decision

                audited = FourWayAuditor().run(
                    request,
                    Decision(
                        request_id=request.request_id, status=DecisionStatus.APPROVED
                    ),
                    transaction_snapshot(request),
                )
                if (
                    request_digest(request) != current.request_digest
                    or not audited.passed
                ):
                    raise ApiError(
                        409,
                        "execution_audit_failed",
                        "Request or mandate changed after the execution claim",
                    )
            if reconcile and current.status == "reserved" and status != "failed":
                raise ApiError(
                    409,
                    "reconciliation_not_required",
                    "An unused reservation can only be cancelled with evidence",
                )
            if reconcile and current.status not in {
                "reserved",
                "submitted",
                "unknown",
                status,
            }:
                raise ApiError(
                    409,
                    "reconciliation_not_required",
                    "Only submitted or unknown executions can be reconciled",
                )
            if not reconcile and current.status == "unknown" and status != "unknown":
                raise ApiError(
                    409,
                    "reconciliation_required",
                    "Unknown execution requires an authorized reconciliation",
                )
            record = self.ledger.transition(
                binding[0],
                status,
                **scope,
                gateway_reference=reference,
                error=error,
                evidence={
                    "source": "operator_reconciliation"
                    if reconcile
                    else "executor_report",
                    "actor": actor,
                    "provider_evidence": evidence or {},
                },
            )
            if status in {"confirmed", "failed"}:
                self._payment_transition(
                    payment,
                    "executed" if status == "confirmed" else "failed",
                    actor,
                    f"execution {record.execution_id}",
                )
            return record.to_dict()

    def legacy_transition(self, payment, state, actor, reason):
        with self.transaction():
            payment = self.payments.get(payment["id"])
            if payment["state"] != "approved":
                raise ApiError(
                    409,
                    "invalid_state_transition",
                    "Legacy execution report requires approved state",
                )
            if self.conn.execute(
                "SELECT 1 FROM execution_bindings WHERE payment_id=?", (payment["id"],)
            ).fetchone():
                raise ApiError(
                    409,
                    "managed_execution_required",
                    "Use execution reporting for a managed payment",
                )
            if state == "executed":
                request, policy, digest, verdict, _ = self.evaluate(payment)
                if (
                    verdict.status is DecisionStatus.NEEDS_REVIEW
                    and self._human_approval(
                        payment, request_digest(request), digest, verdict
                    )
                ):
                    verdict.status = DecisionStatus.APPROVED
                if not verdict.approved:
                    raise ApiError(
                        409, "execution_not_authorized", "; ".join(verdict.reasons)
                    )
                audited = FourWayAuditor().run(
                    request, verdict, transaction_snapshot(request)
                )
                if not audited.passed:
                    raise ApiError(
                        409,
                        "execution_audit_failed",
                        "Legacy report does not satisfy the mandate",
                    )
                existing = self.ledger.get(request.request_id, **self.scope(payment))
                if existing is not None:
                    raise ApiError(
                        409,
                        "managed_execution_required",
                        "Logical action already has an execution claim",
                    )
                request = self.request(payment, legacy=True)
                scope = self.scope(payment)
                claim = self.ledger.claim(
                    request,
                    **scope,
                    request_digest=request_digest(request),
                    policy_digest=digest,
                    decision_id="legacy",
                    context={
                        "provenance": "legacy_external_report",
                        "payment_id": payment["id"],
                        "actor": actor,
                    },
                    daily_budget=policy.daily_budget,
                    monthly_budget=policy.monthly_budget,
                    max_tx_per_hour=policy.max_tx_per_hour,
                )
                if not claim.acquired:
                    raise ApiError(
                        409,
                        "execution_not_authorized",
                        claim.reason or "Execution already claimed",
                    )
                self.ledger.transition(request.request_id, "submitted", **scope)
                self.ledger.transition(
                    request.request_id,
                    "confirmed",
                    **scope,
                    gateway_reference="legacy_external_report",
                    evidence={"provenance": "legacy_external_report", "actor": actor},
                )
            self._payment_transition(payment, state, actor, reason)
        return self.payments.get(payment["id"])

    def evidence(self, payment):
        with self.lock:
            scope = self.scope(payment)
            binding = self.conn.execute(
                "SELECT request_id,policy_version,request_json,policy_json FROM execution_bindings WHERE payment_id=?",
                (payment["id"],),
            ).fetchone()
            request_id = (
                binding[0] if binding else (payment.get("request_id") or payment["id"])
            )
            if not binding and payment["state"] == "executed":
                request_id = payment["id"]
            record = self.ledger.get(request_id, **scope) if binding else None
            active = self.policies.active_for_env(payment["env_id"])
            policy = from_policy(to_policy(active["document"] if active else {}))
            ts = now_ms()
            args = {**scope, "currency": payment["currency"]}
            committed = self.ledger.spend_since(
                payment["agent_id"], ts - 86_400_000, **args, include_reserved=False
            )
            total = self.ledger.spend_since(
                payment["agent_id"], ts - 86_400_000, **args
            )
            monthly = self.ledger.spend_since(
                payment["agent_id"],
                ts - 30 * 86_400_000,
                **args,
                include_reserved=False,
            )
            events = self.ledger.list_events(request_id, **scope, limit=1000)
            return {
                "payment_id": payment["id"],
                "authority": "hosted",
                "legacy_state": payment["state"],
                "record": record.to_dict() if record else None,
                "execution": (
                    {
                        **record.to_dict(),
                        "id": record.execution_id,
                        "logical_action_id": record.request_id,
                        "policy_version": binding[1],
                    }
                    if record
                    else None
                ),
                "reservation": (
                    {
                        "status": "committed"
                        if record.status == "confirmed"
                        else "released"
                        if record.status == "failed"
                        else "held",
                        "amount": record.amount,
                        "currency": record.currency,
                        "expires_at_ms": None,
                    }
                    if record
                    else None
                ),
                "budget": {
                    "scope": {**scope, "agent_id": payment["agent_id"]},
                    "as_of_ms": ts,
                    "window": "rolling_24h",
                    "monthly_window": "rolling_30d",
                    "currency": payment["currency"],
                    "committed_amount": str(committed),
                    "reserved_amount": str(total - committed),
                    "monthly_committed_amount": str(monthly),
                    "monthly_reserved_amount": str(total - committed),
                    "daily_budget": str(policy["daily_budget"])
                    if policy["daily_budget"] is not None
                    else None,
                    "monthly_budget": str(policy["monthly_budget"])
                    if policy["monthly_budget"] is not None
                    else None,
                },
                "request_snapshot": json.loads(binding[2]) if binding else None,
                "policy_snapshot": json.loads(binding[3]) if binding else None,
                "events_limit": 1000,
                "events_total": self.conn.execute(
                    "SELECT COUNT(*) FROM execution_events WHERE org_id=? AND env_id=? AND request_id=?",
                    (scope["org_id"], scope["env_id"], request_id),
                ).fetchone()[0],
                "events_truncated": self.conn.execute(
                    "SELECT COUNT(*) FROM execution_events WHERE org_id=? AND env_id=? AND request_id=?",
                    (scope["org_id"], scope["env_id"], request_id),
                ).fetchone()[0]
                > 1000,
                "events": [
                    {
                        "id": e["event_id"],
                        "type": e["event_type"],
                        "at_ms": e["recorded_at_ms"],
                        "actor": e["payload"]
                        .get("evidence", {})
                        .get(
                            "actor",
                            e["payload"].get("context", {}).get("actor", "system"),
                        ),
                        "payload": e["payload"],
                    }
                    for e in events
                ],
            }
