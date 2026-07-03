"""Payment policy and budget tracking.

PaymentPolicy is the declarative configuration the decision engine
evaluates against. BudgetTracker keeps rolling spend windows and is
pluggable so production deployments can back it with Redis/Postgres.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from ..models import _require_currency, _require_positive_number


@dataclass
class PaymentPolicy:
    """Declarative spend policy for an agent (or fleet of agents)."""

    # Threshold checks
    review_threshold: float = 100.0       # amounts above this need human review
    hard_limit: float = 1000.0            # amounts above this are rejected outright

    # Merchant controls
    merchant_allowlist: Optional[set[str]] = None  # if set, only these may be paid
    merchant_blocklist: set[str] = field(default_factory=set)
    known_merchants: set[str] = field(default_factory=set)  # for unknown-merchant detection
    treat_unknown_merchant_as: str = "needs_review"  # or "rejected"

    # Budget controls
    daily_budget: Optional[float] = None
    monthly_budget: Optional[float] = None
    budget_warning_ratio: float = 0.8     # flag when projected spend crosses this share

    # Review-required logic
    review_categories: set[str] = field(default_factory=set)   # e.g. {"gift_cards", "crypto"}
    allowed_currencies: set[str] = field(default_factory=lambda: {"USD"})
    max_tx_per_hour: Optional[int] = None  # velocity guard

    def __post_init__(self) -> None:
        _require_positive_number(self.review_threshold, "PaymentPolicy.review_threshold")
        _require_positive_number(self.hard_limit, "PaymentPolicy.hard_limit")
        if self.hard_limit < self.review_threshold:
            raise ValueError(
                "PaymentPolicy.hard_limit must be >= review_threshold "
                f"({self.hard_limit} < {self.review_threshold})"
            )
        if not 0 < self.budget_warning_ratio <= 1:
            raise ValueError(
                "PaymentPolicy.budget_warning_ratio must be in (0, 1], got "
                f"{self.budget_warning_ratio!r}"
            )
        if self.daily_budget is not None:
            _require_positive_number(self.daily_budget, "PaymentPolicy.daily_budget")
        if self.monthly_budget is not None:
            _require_positive_number(self.monthly_budget, "PaymentPolicy.monthly_budget")
        if self.max_tx_per_hour is not None and self.max_tx_per_hour < 1:
            raise ValueError(
                f"PaymentPolicy.max_tx_per_hour must be >= 1, got {self.max_tx_per_hour!r}"
            )
        if self.treat_unknown_merchant_as not in ("needs_review", "rejected"):
            raise ValueError(
                "PaymentPolicy.treat_unknown_merchant_as must be 'needs_review' or "
                f"'rejected', got {self.treat_unknown_merchant_as!r}"
            )
        for code in self.allowed_currencies:
            _require_currency(code, "PaymentPolicy.allowed_currencies entry")

    def normalized_allowlist(self) -> Optional[set[str]]:
        if self.merchant_allowlist is None:
            return None
        return {m.strip().lower() for m in self.merchant_allowlist}

    def normalized_blocklist(self) -> set[str]:
        return {m.strip().lower() for m in self.merchant_blocklist}

    def normalized_known(self) -> set[str]:
        known = {m.strip().lower() for m in self.known_merchants}
        if self.merchant_allowlist:
            known |= self.normalized_allowlist() or set()
        return known


class BudgetStore(Protocol):
    """Pluggable spend store. Implement with Redis/Postgres in production."""

    def record_spend(self, agent_id: str, amount: float, ts: Optional[float] = None) -> None: ...
    def spend_since(self, agent_id: str, since_ts: float) -> float: ...
    def tx_count_since(self, agent_id: str, since_ts: float) -> int: ...


class InMemoryBudgetStore:
    """Thread-safe in-memory spend ledger (per-process; for dev/tests)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ledger: dict[str, list[tuple[float, float]]] = {}

    def record_spend(self, agent_id: str, amount: float, ts: Optional[float] = None) -> None:
        with self._lock:
            self._ledger.setdefault(agent_id, []).append((ts or time.time(), amount))

    def spend_since(self, agent_id: str, since_ts: float) -> float:
        with self._lock:
            return sum(a for t, a in self._ledger.get(agent_id, []) if t >= since_ts)

    def tx_count_since(self, agent_id: str, since_ts: float) -> int:
        with self._lock:
            return sum(1 for t, _ in self._ledger.get(agent_id, []) if t >= since_ts)


class BudgetTracker:
    """Evaluates daily/monthly budgets and transaction velocity."""

    DAY = 86_400.0
    MONTH = 30 * 86_400.0
    HOUR = 3_600.0

    def __init__(self, store: Optional[BudgetStore] = None) -> None:
        self.store: BudgetStore = store or InMemoryBudgetStore()

    def daily_spend(self, agent_id: str) -> float:
        return self.store.spend_since(agent_id, time.time() - self.DAY)

    def monthly_spend(self, agent_id: str) -> float:
        return self.store.spend_since(agent_id, time.time() - self.MONTH)

    def hourly_tx_count(self, agent_id: str) -> int:
        return self.store.tx_count_since(agent_id, time.time() - self.HOUR)

    def commit(self, agent_id: str, amount: float) -> None:
        """Record spend after a successful execution."""
        self.store.record_spend(agent_id, amount)
