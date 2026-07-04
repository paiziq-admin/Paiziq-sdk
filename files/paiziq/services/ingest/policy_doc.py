"""Policy document conversion and validation (contract §10).

A policy *document* is the JSON wire form of the SDK `PaymentPolicy`:
sets become sorted lists, optionals stay null. Validation delegates to
`PaymentPolicy.__post_init__`, so the API accepts exactly what the
decision engine accepts — one source of truth for policy rules.
"""

from __future__ import annotations

from typing import Any, Optional

from paiziq import PaymentPolicy

# Document fields, in wire order. `merchant_allowlist` is nullable
# (null = no allowlist); every other list field defaults to empty.
_SET_FIELDS = ("merchant_blocklist", "known_merchants", "review_categories",
               "allowed_currencies")
_SCALAR_FIELDS = ("review_threshold", "hard_limit", "treat_unknown_merchant_as",
                  "daily_budget", "monthly_budget", "budget_warning_ratio",
                  "max_tx_per_hour")
DOCUMENT_FIELDS = ("merchant_allowlist",) + _SET_FIELDS + _SCALAR_FIELDS


class DocumentError(ValueError):
    """Raised when a policy document is malformed."""


def to_policy(document: dict[str, Any]) -> PaymentPolicy:
    """Build a validated PaymentPolicy from a document; raise DocumentError."""
    unknown = set(document) - set(DOCUMENT_FIELDS)
    if unknown:
        raise DocumentError(f"unknown policy fields: {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    allowlist = document.get("merchant_allowlist")
    if allowlist is not None:
        if not isinstance(allowlist, list):
            raise DocumentError("merchant_allowlist must be a list or null")
        kwargs["merchant_allowlist"] = {str(m) for m in allowlist}
    for name in _SET_FIELDS:
        if name in document:
            value = document[name]
            if not isinstance(value, list):
                raise DocumentError(f"{name} must be a list")
            kwargs[name] = {str(m) for m in value}
    for name in _SCALAR_FIELDS:
        if name in document and document[name] is not None:
            kwargs[name] = document[name]
    try:
        return PaymentPolicy(**kwargs)
    except (ValueError, TypeError) as exc:
        raise DocumentError(str(exc)) from exc


def from_policy(policy: PaymentPolicy) -> dict[str, Any]:
    """Canonical document for a PaymentPolicy (sorted lists, stable keys)."""
    return {
        "merchant_allowlist": (
            sorted(policy.merchant_allowlist) if policy.merchant_allowlist is not None else None
        ),
        "merchant_blocklist": sorted(policy.merchant_blocklist),
        "known_merchants": sorted(policy.known_merchants),
        "review_categories": sorted(policy.review_categories),
        "allowed_currencies": sorted(policy.allowed_currencies),
        "review_threshold": policy.review_threshold,
        "hard_limit": policy.hard_limit,
        "treat_unknown_merchant_as": policy.treat_unknown_merchant_as,
        "daily_budget": policy.daily_budget,
        "monthly_budget": policy.monthly_budget,
        "budget_warning_ratio": policy.budget_warning_ratio,
        "max_tx_per_hour": policy.max_tx_per_hour,
    }


def normalize(document: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Validate a document (default policy when None) and return its
    canonical form — what gets persisted and published."""
    return from_policy(to_policy(document or {}))
