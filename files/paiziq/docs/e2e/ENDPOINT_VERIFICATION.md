# Endpoint verification

Executed: 2026-10-03T21:14:07.092540+00:00
Base URL: `http://127.0.0.1:8800`

| Method | Path | Actual status (expected 200) | Verified | Result |
| --- | --- | --- | --- | --- |
| POST | `/v1/orgs` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/orgs/org_592948de1377ef398c38/environments` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/agents` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/policies` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/policies/pol_742cbc9f2c1f1a7bb695/publish` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/payments` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/traces` | 200 | SDK spans accepted | pass |
| POST | `/v1/decisions` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/traces` | 200 | SDK spans accepted | pass |
| POST | `/v1/payments/pay_e958170db08f3048b443/transition` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/traces` | 200 | SDK spans accepted | pass |
| POST | `/v1/payments` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/decisions` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/traces` | 200 | SDK spans accepted | pass |
| POST | `/v1/payments` | 200 | success envelope and persisted resource fields | pass |
| POST | `/v1/decisions` | 200 | success envelope and persisted resource fields | pass |
| GET | `/health` | 200 | health: envelope, fields, scenario consistency | pass |
| GET | `/v1/orgs?limit=200` | 200 | orgs: envelope, fields, scenario consistency | pass |
| GET | `/v1/orgs/org_592948de1377ef398c38/environments?limit=200` | 200 | environments: envelope, fields, scenario consistency | pass |
| GET | `/v1/agents?env_id=env_a69025df93990ac31820&limit=200` | 200 | agents: envelope, fields, scenario consistency | pass |
| GET | `/v1/payments?env_id=env_a69025df93990ac31820&sort=created_desc&limit=8` | 200 | payments: envelope, fields, scenario consistency | pass |
| GET | `/v1/payments/pay_7bd7050eb7321e5eaf30` | 200 | payment: envelope, fields, scenario consistency | pass |
| GET | `/v1/decisions?payment_id=pay_7bd7050eb7321e5eaf30` | 200 | decisions: envelope, fields, scenario consistency | pass |
| GET | `/v1/decisions/dec_b886124846cf7220db1f` | 200 | decision: envelope, fields, scenario consistency | pass |
| GET | `/v1/traces/e2e-req-t2-review-env_a69025df93990ac31820` | 200 | trace-miss: envelope, fields, scenario consistency | pass |
| GET | `/v1/traces/pay_7bd7050eb7321e5eaf30` | 200 | trace-miss: envelope, fields, scenario consistency | pass |
| GET | `/v1/search/events?q=%22e2e-req-t2-review-env_a69025df93990ac31820%22&limit=10` | 200 | search: envelope, fields, scenario consistency | pass |
| GET | `/v1/traces/d1202e28243446808fe7d5edf92b74f3` | 200 | trace: envelope, fields, scenario consistency | pass |
| GET | `/v1/reviews?state=open&env_id=env_a69025df93990ac31820` | 200 | reviews: envelope, fields, scenario consistency | pass |
| GET | `/v1/reviews/rev_c9d06da54e2e6e6c17c5` | 200 | review: envelope, fields, scenario consistency | pass |
| GET | `/v1/reviews/identity` | 200 | identity: envelope, fields, scenario consistency | pass |
| GET | `/v1/policies?env_id=env_a69025df93990ac31820` | 200 | policies: envelope, fields, scenario consistency | pass |
| GET | `/v1/policies/pol_742cbc9f2c1f1a7bb695` | 200 | policy: envelope, fields, scenario consistency | pass |
| GET | `/v1/policies/pol_742cbc9f2c1f1a7bb695/versions` | 200 | versions: envelope, fields, scenario consistency | pass |
| POST | `/v1/policies/simulate` | 200 | simulation: envelope, fields, scenario consistency | pass |
| GET | `/v1/metrics/summary?env_id=env_a69025df93990ac31820` | 200 | summary: envelope, fields, scenario consistency | pass |
| GET | `/v1/metrics/timeseries?env_id=env_a69025df93990ac31820&metric=payments.total&interval=1h` | 200 | timeseries: envelope, fields, scenario consistency | pass |
| GET | `/v1/audit-logs?limit=5` | 200 | audit: envelope, fields, scenario consistency | pass |
| GET | `/v1/notifications` | 200 | notifications: envelope, fields, scenario consistency | pass |
| GET | `/v1/webhook-deliveries?env_id=env_a69025df93990ac31820&payment_id=pay_7bd7050eb7321e5eaf30&limit=200` | 200 | deliveries: envelope, fields, scenario consistency | pass |

Local SDK and server verdicts, reasons, flags, final states, policy version, and mock execution checked.

```json
{
  "base_url": "http://127.0.0.1:8800",
  "org": {
    "id": "org_592948de1377ef398c38",
    "name": "e2e-org-ea6fbf5f"
  },
  "environment": {
    "id": "env_a69025df93990ac31820",
    "name": "e2e-sandbox",
    "kind": "sandbox"
  },
  "agent": {
    "id": "agt_467e444f4f13e23c881e",
    "name": "e2e-payment-agent"
  },
  "policy": {
    "id": "pol_742cbc9f2c1f1a7bb695",
    "name": "e2e-threshold-policy",
    "version": 1
  },
  "transactions": [
    {
      "key": "t1",
      "request_id": "e2e-req-t1-approved-env_a69025df93990ac31820",
      "merchant": "acme corp",
      "amount": 49.99,
      "intent": "Renew Acme subscription",
      "payment_id": "pay_e958170db08f3048b443",
      "state": "executed",
      "decision_id": "dec_fadf02f2ef01021b04c2",
      "verdict": "approved",
      "reasons": [
        "All decision rules passed"
      ],
      "risk_flags": [],
      "policy_version": 1,
      "review_id": null,
      "trace_id": "b7e516d5039b419c934ef2931e0df21b",
      "gateway_reference": "mock_9453e09fdad6",
      "local_verdict": "approved",
      "local_reasons": [
        "All decision rules passed"
      ],
      "local_risk_flags": []
    },
    {
      "key": "t2",
      "request_id": "e2e-req-t2-review-env_a69025df93990ac31820",
      "merchant": "cloudhost inc",
      "amount": 180.0,
      "intent": "Annual CloudHost renewal",
      "payment_id": "pay_7bd7050eb7321e5eaf30",
      "state": "needs_review",
      "decision_id": "dec_b886124846cf7220db1f",
      "verdict": "needs_review",
      "reasons": [
        "Amount 180.00 exceeds review threshold 100.00; human approval required"
      ],
      "risk_flags": [
        "over_review_threshold"
      ],
      "policy_version": 1,
      "review_id": "rev_c9d06da54e2e6e6c17c5",
      "trace_id": "d1202e28243446808fe7d5edf92b74f3",
      "gateway_reference": null,
      "local_verdict": "needs_review",
      "local_reasons": [
        "Amount 180.00 exceeds review threshold 100.00; human approval required"
      ],
      "local_risk_flags": [
        "over_review_threshold"
      ]
    },
    {
      "key": "t3",
      "request_id": "e2e-req-t3-rejected-env_a69025df93990ac31820",
      "merchant": "shady llc",
      "amount": 20.0,
      "intent": "Purchase from a blocklisted merchant",
      "payment_id": "pay_8477a7d4320a11b6a186",
      "state": "rejected",
      "decision_id": "dec_a9cc21c8b1134c8b78fe",
      "verdict": "rejected",
      "reasons": [
        "Merchant 'shady llc' is blocklisted"
      ],
      "risk_flags": [
        "merchant_blocked"
      ],
      "policy_version": 1,
      "review_id": null,
      "trace_id": "5de71c2f9ed040a9a7bd0095080c0f61",
      "gateway_reference": null,
      "local_verdict": "rejected",
      "local_reasons": [
        "Merchant 'shady llc' is blocklisted"
      ],
      "local_risk_flags": [
        "merchant_blocked"
      ]
    }
  ]
}
```

All recorded checks passed.
