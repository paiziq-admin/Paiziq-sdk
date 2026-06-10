"""Built-in decision rules.

Each rule is a small, independently testable callable that inspects a
PaymentRequest against a PaymentPolicy and returns a RuleResult. The
DecisionEngine aggregates results with severity ordering:
rejected > needs_review > approved.
"""

from __future__ import annotations

import re
from typing import Optional, Protocol

from ..models import DecisionStatus, PaymentRequest, RiskFlag, RuleResult
from .policy import BudgetTracker, PaymentPolicy

APPROVED = DecisionStatus.APPROVED
REVIEW = DecisionStatus.NEEDS_REVIEW
REJECTED = DecisionStatus.REJECTED


class Rule(Protocol):
    """Custom rules implement this protocol and register on the engine."""

    name: str

    def evaluate(self, request: PaymentRequest, policy: PaymentPolicy) -> RuleResult: ...


class ThresholdRule:
    """Amount thresholds: hard limit -> rejected, review threshold -> needs_review."""

    name = "threshold_check"

    def evaluate(self, request: PaymentRequest, policy: PaymentPolicy) -> RuleResult:
        amt = request.amount
        if amt <= 0:
            return RuleResult(
                self.name, REJECTED,
                reasons=[f"Amount must be positive, got {amt}"],
                details={"amount": amt},
            )
        if amt > policy.hard_limit:
            return RuleResult(
                self.name, REJECTED,
                reasons=[f"Amount {amt:.2f} exceeds hard limit {policy.hard_limit:.2f}"],
                risk_flags=[RiskFlag.OVER_HARD_LIMIT],
                details={"amount": amt, "hard_limit": policy.hard_limit},
            )
        if amt > policy.review_threshold:
            return RuleResult(
                self.name, REVIEW,
                reasons=[
                    f"Amount {amt:.2f} exceeds review threshold "
                    f"{policy.review_threshold:.2f}; human approval required"
                ],
                risk_flags=[RiskFlag.OVER_REVIEW_THRESHOLD],
                details={"amount": amt, "review_threshold": policy.review_threshold},
            )
        return RuleResult(self.name, APPROVED, reasons=["Amount within auto-approve threshold"])


class MerchantListRule:
    """Blocklist always rejects; if an allowlist exists, off-list merchants are rejected."""

    name = "merchant_list_check"

    def evaluate(self, request: PaymentRequest, policy: PaymentPolicy) -> RuleResult:
        merchant = request.merchant.strip().lower()
        if merchant in policy.normalized_blocklist():
            return RuleResult(
                self.name, REJECTED,
                reasons=[f"Merchant '{request.merchant}' is blocklisted"],
                risk_flags=[RiskFlag.MERCHANT_BLOCKED],
                details={"merchant": request.merchant},
            )
        allowlist = policy.normalized_allowlist()
        if allowlist is not None and merchant not in allowlist:
            return RuleResult(
                self.name, REJECTED,
                reasons=[f"Merchant '{request.merchant}' is not on the approved allowlist"],
                risk_flags=[RiskFlag.MERCHANT_NOT_ALLOWLISTED],
                details={"merchant": request.merchant},
            )
        return RuleResult(self.name, APPROVED, reasons=["Merchant passes list checks"])


class UnknownMerchantRule:
    """Merchants never seen before are escalated (or rejected, per policy)."""

    name = "unknown_merchant_check"

    def evaluate(self, request: PaymentRequest, policy: PaymentPolicy) -> RuleResult:
        merchant = request.merchant.strip().lower()
        known = policy.normalized_known()
        if not known:  # detection disabled when no known-merchant set configured
            return RuleResult(self.name, APPROVED, reasons=["Unknown-merchant detection disabled"])
        if merchant in known:
            return RuleResult(self.name, APPROVED, reasons=["Merchant is known"])
        status = REJECTED if policy.treat_unknown_merchant_as == "rejected" else REVIEW
        return RuleResult(
            self.name, status,
            reasons=[f"Merchant '{request.merchant}' has not been seen before"],
            risk_flags=[RiskFlag.UNKNOWN_MERCHANT],
            details={"merchant": request.merchant},
        )


class BudgetRule:
    """Validates daily/monthly budgets including the pending amount."""

    name = "budget_check"

    def __init__(self, tracker: Optional[BudgetTracker] = None) -> None:
        self.tracker = tracker or BudgetTracker()

    def evaluate(self, request: PaymentRequest, policy: PaymentPolicy) -> RuleResult:
        reasons: list[str] = []
        flags: list[RiskFlag] = []
        details: dict = {}
        status = APPROVED

        for label, budget, spent in (
            ("daily", policy.daily_budget, self.tracker.daily_spend(request.agent_id)),
            ("monthly", policy.monthly_budget, self.tracker.monthly_spend(request.agent_id)),
        ):
            if budget is None:
                continue
            projected = spent + request.amount
            details[f"{label}_spent"] = round(spent, 2)
            details[f"{label}_budget"] = budget
            details[f"{label}_projected"] = round(projected, 2)
            if projected > budget:
                status = REJECTED
                flags.append(RiskFlag.BUDGET_EXCEEDED)
                reasons.append(
                    f"Payment would exceed {label} budget: "
                    f"{projected:.2f} > {budget:.2f} (spent {spent:.2f})"
                )
            elif projected > budget * policy.budget_warning_ratio and status is not REJECTED:
                status = REVIEW
                flags.append(RiskFlag.BUDGET_NEAR_LIMIT)
                reasons.append(
                    f"Payment pushes {label} spend past "
                    f"{int(policy.budget_warning_ratio * 100)}% of budget "
                    f"({projected:.2f}/{budget:.2f})"
                )

        if not reasons:
            reasons = ["Within configured budgets"]
        return RuleResult(self.name, status, reasons=reasons, risk_flags=flags, details=details)


class ReviewRequiredRule:
    """Category, currency, and velocity conditions that force human review."""

    name = "review_required_check"

    def __init__(self, tracker: Optional[BudgetTracker] = None) -> None:
        self.tracker = tracker or BudgetTracker()

    def evaluate(self, request: PaymentRequest, policy: PaymentPolicy) -> RuleResult:
        reasons: list[str] = []
        flags: list[RiskFlag] = []
        status = APPROVED

        if request.category.strip().lower() in {c.lower() for c in policy.review_categories}:
            status = REVIEW
            flags.append(RiskFlag.CATEGORY_REVIEW_REQUIRED)
            reasons.append(f"Category '{request.category}' always requires review")

        if policy.allowed_currencies and request.currency.upper() not in {
            c.upper() for c in policy.allowed_currencies
        }:
            status = REVIEW
            flags.append(RiskFlag.CURRENCY_NOT_PERMITTED)
            reasons.append(f"Currency '{request.currency}' is outside permitted set")

        if policy.max_tx_per_hour is not None:
            count = self.tracker.hourly_tx_count(request.agent_id)
            if count >= policy.max_tx_per_hour:
                status = REVIEW
                flags.append(RiskFlag.VELOCITY_ANOMALY)
                reasons.append(
                    f"Agent executed {count} transactions in the past hour "
                    f"(limit {policy.max_tx_per_hour}); velocity anomaly"
                )

        if not reasons:
            reasons = ["No review-required conditions triggered"]
        return RuleResult(self.name, status, reasons=reasons, risk_flags=flags)


class HarmfulIntentRule:
    """Heuristic screen of the agent's stated intent for harmful/abusive patterns.

    Deliberately conservative: it escalates to review and raises a risk
    flag (which triggers user notification) rather than silently rejecting.
    The dashboard-side LLM judge performs deeper semantic analysis.
    """

    name = "harmful_intent_check"

    _PATTERNS = [
        r"\bgift\s*cards?\b.*\bbulk\b|\bbulk\b.*\bgift\s*cards?\b",
        r"\buntraceab(le|ility)\b",
        r"\bavoid\b.*\b(detection|review|audit|limits?)\b",
        r"\bsplit(ting)?\b.*\b(payments?|transactions?)\b.*\b(limit|threshold)\b",
        r"\bwithout\b.*\b(approval|authoriz|permission)\b",
        r"\bhide\b.*\b(transaction|payment|spend)\b",
        r"\blaunder",
        r"\bsanction(ed)?\b",
    ]

    def evaluate(self, request: PaymentRequest, policy: PaymentPolicy) -> RuleResult:
        text = f"{request.intent_description} {request.metadata.get('agent_reasoning', '')}".lower()
        hits = [p for p in self._PATTERNS if re.search(p, text)]
        if hits:
            return RuleResult(
                self.name, REVIEW,
                reasons=["Agent intent matches harmful/evasive patterns; flagged for review"],
                risk_flags=[RiskFlag.HARMFUL_INTENT_SUSPECTED],
                details={"matched_patterns": len(hits)},
            )
        return RuleResult(self.name, APPROVED, reasons=["No harmful-intent indicators"])


def default_rules(tracker: Optional[BudgetTracker] = None) -> list[Rule]:
    tracker = tracker or BudgetTracker()
    return [
        ThresholdRule(),
        MerchantListRule(),
        UnknownMerchantRule(),
        BudgetRule(tracker),
        ReviewRequiredRule(tracker),
        HarmfulIntentRule(),
    ]
