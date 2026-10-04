"""Single source of truth for the local payment-agent workflow.

Every lane uses these merchants, amounts, request ids, and the verdicts
the SDK DecisionEngine produces for POLICY_DOCUMENT. shady llc is also a
known merchant so the blocklist case has exactly one risk flag.
"""

from __future__ import annotations

POLICY_NAME = "e2e-threshold-policy"
AGENT_NAME = "e2e-payment-agent"
PRINCIPAL_ID = "user-42"
ENV_NAME = "e2e-sandbox"
ENV_KIND = "sandbox"

POLICY_DOCUMENT: dict = {
    "merchant_allowlist": None,
    "merchant_blocklist": ["shady llc"],
    "known_merchants": ["acme corp", "cloudhost inc", "shady llc"],
    "review_categories": [],
    "allowed_currencies": ["USD"],
    "review_threshold": 100,
    "hard_limit": 1000,
    "treat_unknown_merchant_as": "needs_review",
    "daily_budget": 5000,
    "monthly_budget": None,
    "budget_warning_ratio": 0.8,
    "max_tx_per_hour": 20,
}

# Reason text is the engine format in sdk/src/paiziq/engine/rules.py.
TRANSACTIONS: list[dict] = [
    {
        "key": "t1",
        "request_id": "e2e-req-t1-approved",
        "merchant": "acme corp",
        "amount": 49.99,
        "intent": "Renew Acme subscription",
        "execute": True,
        "expected_verdict": "approved",
        "expected_state": "executed",
        "expected_reasons": ["All decision rules passed"],
        "expected_flags": [],
    },
    {
        "key": "t2",
        "request_id": "e2e-req-t2-review",
        "merchant": "cloudhost inc",
        "amount": 180.0,
        "intent": "Annual CloudHost renewal",
        "execute": False,
        "expected_verdict": "needs_review",
        "expected_state": "needs_review",
        "expected_reasons": [
            "Amount 180.00 exceeds review threshold 100.00; human approval required"
        ],
        "expected_flags": ["over_review_threshold"],
    },
    {
        "key": "t3",
        "request_id": "e2e-req-t3-rejected",
        "merchant": "shady llc",
        "amount": 20.0,
        "intent": "Purchase from a blocklisted merchant",
        "execute": False,
        "expected_verdict": "rejected",
        "expected_state": "rejected",
        "expected_reasons": ["Merchant 'shady llc' is blocklisted"],
        "expected_flags": ["merchant_blocked"],
    },
]
