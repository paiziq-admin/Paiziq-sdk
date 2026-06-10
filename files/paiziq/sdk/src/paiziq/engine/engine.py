"""DecisionEngine: runs all rules and aggregates a single verdict.

Severity ordering: rejected > needs_review > approved. All reasons and
risk flags from every rule are preserved on the final Decision so the
dashboard can render a full explanation, not just the verdict.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..models import Decision, DecisionStatus, PaymentRequest, RiskFlag, RuleResult
from .policy import BudgetTracker, PaymentPolicy
from .rules import Rule, default_rules

_SEVERITY = {
    DecisionStatus.APPROVED: 0,
    DecisionStatus.NEEDS_REVIEW: 1,
    DecisionStatus.REJECTED: 2,
}


class DecisionEngine:
    def __init__(
        self,
        policy: Optional[PaymentPolicy] = None,
        rules: Optional[Iterable[Rule]] = None,
        budget_tracker: Optional[BudgetTracker] = None,
    ) -> None:
        self.policy = policy or PaymentPolicy()
        self.budget_tracker = budget_tracker or BudgetTracker()
        self.rules: list[Rule] = list(rules) if rules is not None else default_rules(self.budget_tracker)

    def add_rule(self, rule: Rule) -> None:
        """Register a custom rule (must satisfy the Rule protocol)."""
        self.rules.append(rule)

    def evaluate(self, request: PaymentRequest) -> Decision:
        results: list[RuleResult] = [rule.evaluate(request, self.policy) for rule in self.rules]

        final = DecisionStatus.APPROVED
        reasons: list[str] = []
        flags: list[RiskFlag] = []
        for r in results:
            if _SEVERITY[r.status] > _SEVERITY[final]:
                final = r.status
            if r.status is not DecisionStatus.APPROVED:
                reasons.extend(r.reasons)
            for f in r.risk_flags:
                if f not in flags:
                    flags.append(f)

        if final is DecisionStatus.APPROVED:
            reasons = ["All decision rules passed"]

        return Decision(
            request_id=request.request_id,
            status=final,
            reasons=reasons,
            risk_flags=flags,
            rule_results=results,
        )
