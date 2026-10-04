# Paiziq: from payment intent to an explainable decision

A hands-on local tutorial, verified 2026-10-03 with SDK 0.2.0. Allow about ten
minutes after dependencies are installed. The gateway is simulated; no real
payment credentials or money are involved.

## 1. Prepare the two repositories

Use Python 3.10+ and Node `^22.22.0` or `>=24`. The verified machine used Python
3.14 and Node 24.19.0. Change these paths if your checkouts are elsewhere.

```bash
cd /Users/chavz/Documents/paiziq_backend/files/paiziq
# First-time setup only; skip if the existing environment is installed.
make venv
make install
make ingest-install

cd /Users/chavz/Documents/Paiziq-Dashboard
npm ci
npx playwright install chromium
```

If this machine's shell reports Node 23.6.0, a supported runtime is already
available. In each dashboard terminal use:

```bash
export PATH="/Users/chavz/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH"
node --version
```

## 2. Start the local backend and dashboard

Backend terminal:

```bash
cd /Users/chavz/Documents/paiziq_backend/files/paiziq
make e2e-stack
```

Keep it open. This uses `.e2e/paiziq-e2e.sqlite`, loopback port 8800, development
bootstrap key `dev-key`, and CORS for `http://127.0.0.1:4173`.

Dashboard terminal:

```bash
cd /Users/chavz/Documents/Paiziq-Dashboard
npm run dev -- --host 127.0.0.1 --port 4173 --strictPort
```

## 3. Let the SDK agent run the scenario

In another backend terminal:

```bash
cd /Users/chavz/Documents/paiziq_backend/files/paiziq
mkdir -p .e2e
make -s e2e-seed > .e2e/latest-workflow.json
cat .e2e/latest-workflow.json
```

The JSON identifies your organization, environment, agent, published policy,
payments, decisions, review, and traces. Record `org.name` and select that
organization in the dashboard. Every invocation creates a fresh run; IDs differ.

The agent registers an organization/environment/agent, publishes policy version 1,
and proposes three payments. Its SDK uses the same policy document as the server:

| Merchant | USD | SDK/server verdict | Dashboard state | What happened |
| --- | ---: | --- | --- | --- |
| acme corp | 49.99 | approved | Executed | 4-Way Match passed; MockGateway returned a reference |
| cloudhost inc | 180.00 | needs_review | Needs Review | Above the 100 USD threshold; open human review |
| shady llc | 20.00 | rejected | Rejected | Merchant blocklist; no execution |

Only T1 executes. `gateway_reference` is populated for that transaction; T2 and
T3 remain unexecuted. All decisions include reasons and risk flags.

## 4. Connect and select the run

Open `http://127.0.0.1:4173/login`. Enter Backend URL
`http://127.0.0.1:8800` and API key `dev-key`, then **Connect dashboard**.
Choose the `e2e-org-…` name from your JSON and environment **e2e-sandbox** in the
top bar. Leave the time range at **Last 24 hours** for newly seeded data.

**Overview** summarizes the selected environment. **Agents & SDK** shows
`e2e-payment-agent`. The dashboard's separate **Demo mode** is a data-free preview;
use the live connection for this populated tutorial.

## 5. Inspect the payment and decision

Open **Payment Feed**. You should see exactly the three rows above. Click the
payment ID for **cloudhost inc / 180 USD** to open its detail.

Look for:

- **Needs Review**, including the transition from Proposed.
- **Policy version 1** in Decisions.
- `Amount 180.00 exceeds review threshold 100.00; human approval required`.
- `over_review_threshold`.
- `paiziq.review_payment` in the timeline and **Agent Trace JSON**.

The request ID links the agent's intent, persisted payment, and trace event. The
trace ID is a separate identifier. Search **Agent Trace JSON** for the request ID
from your JSON to inspect the matching event; use **Copy redacted JSON** to export
what is displayed. Trace search currently correlates through indexed event payloads.

## 6. Inspect the human review

Open **Human Reviews**. The CloudHost payment appears in the open queue with its
reason, priority, and SLA. Leave it open to preserve the three baseline outcomes.

For a later manual review exercise, a bootstrap admin supplies an **Acting
reviewer** label, claims the review, adds **Reviewer notes**, and chooses **Approve**
or **Decline**. Approval changes the control-plane payment state; it does not charge
a gateway. There is currently no automatic bridge from that dashboard approval to
a running SDK process's local `approve_review`. This tutorial's live service test
verifies the open queue; existing fixture and backend review tests cover resolution.

## 7. Explore the policy safely

Open **Risk Policies** and choose **e2e-threshold-policy**. **Active v1** and the
**Review threshold** of 100 identify the document used for the recorded decisions.
The hard limit is 1000; the merchant blocklist contains `shady llc`.

Choose **Simulator** and enter:

| Field | Value |
| --- | --- |
| Merchant | cloudhost inc |
| Amount | 180 |
| Currency | USD |
| Intent description | Annual CloudHost renewal |
| Policy source | Version 1 (active) |

Click **Simulate decision**. It returns **needs review**, the same threshold reason,
and **Not persisted**. Simulation adds no payment or decision. The **Current
in-browser draft** source evaluates unsaved edits; **Version 1** uses the immutable
published document. To change actual future decisions, save a draft with an audit
reason, then publish it; changing a field alone does not publish a policy.

## 8. Use the Python library in your own agent

This minimal example runs locally with the SDK and a mock gateway. It illustrates
the library boundary; the full-stack seeder adds the HTTP persistence calls below.

```python
from paiziq import PaiziqSDK, PaymentPolicy, PaymentRequest
from paiziq.audit import MockGateway

sdk = PaiziqSDK(
    policy=PaymentPolicy(
        review_threshold=100,
        hard_limit=1000,
        known_merchants={"acme corp", "cloudhost inc", "shady llc"},
        merchant_blocklist={"shady llc"},
        allowed_currencies={"USD"},
        daily_budget=5000,
        max_tx_per_hour=20,
    ),
    gateway=MockGateway(),
    exporters=[],  # Explicitly local for this small example.
    service_name="my-payment-agent",
)
try:
    request = PaymentRequest(
        agent_id="my-payment-agent",
        principal_id="user-42",
        merchant="acme corp",
        amount=49.99,
        currency="USD",
        intent_description="Renew Acme subscription",
    )
    decision = sdk.review_payment(request)
    print(decision.status.value, decision.reasons)
    if decision.status.value == "approved":
        result = sdk.execute_payment(request)
        print(result.executed, result.gateway_reference)
    print(sdk.get_audit_trail(request.request_id))
finally:
    sdk.shutdown()
```

The implementation to copy/adapt for the full workflow is
`services/ingest/tests/e2e_support/simulated_agent.py`. It explicitly:

1. Creates organization, environment, agent, and policy; publishes policy v1.
2. Configures `HTTPExporter` with an authenticated `SyncHTTPTransport`.
3. Calls `review_payment` and posts `/v1/payments` with the same `request_id` and
   an idempotency key, then posts `/v1/decisions` for the server's published-policy verdict.
4. Requires local/server approval before executing via `MockGateway`, checks
   `ExecutionResult.executed`, then records the `executed` transition.
5. Calls `shutdown()` to flush exports and waits for trace ingestion.

An HTTP exporter alone sends telemetry; it does not create the payment feed records.
Do not use the local `dev-key` bootstrap setup as a production deployment recipe.

## 9. Reproduce the verification and screenshots

While the manual backend is running:

```bash
# Backend directory
make e2e-verify
make e2e-test
make check
```

The verifier creates a fresh scenario and writes
`docs/e2e/ENDPOINT_VERIFICATION.md`; its table includes real statuses, envelope,
required-field and scenario-consistency checks. Exit status is nonzero on failure.

Stop the manual backend and Vite with Ctrl-C before running the automated browser
stack. Then, from the dashboard:

```bash
npm run test:e2e
npm run check
PAIZIQ_DEMO_DIR="$PWD/test-results/tutorial" npm run test:e2e:service
```

The service test starts its own servers/database, uses the real SDK, and captures
nine PNG screens plus `workflow.json` when `PAIZIQ_DEMO_DIR` is set. It shuts down
its servers afterward. For a custom checkout set `PAIZIQ_BACKEND_DIR` to the
absolute backend `files/paiziq` directory.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Node engine warning | Use Node ^22.22 or >=24; the verified bundled runtime is 24.19.0 |
| Port already in use in the service test | Stop the manual 8800/4173 servers; the test deliberately refuses to reuse them |
| Browser cannot connect | Use exactly 127.0.0.1:4173 and 127.0.0.1:8800; match CORS to the browser origin |
| No payments visible | Select the organization from the latest JSON and e2e-sandbox; use a range containing the run |
| No correlated trace on older records | Use the payload-indexing fix and seed fresh data; old indexes are not backfilled by this patch |
| Need to repeat the demo | Rerun e2e-seed and select its new organization; there is no need to delete a database |

The included interactive HTML guide embeds real captured screenshots and works
offline. Screenshots are a recorded walkthrough, not a connection to a live service.
