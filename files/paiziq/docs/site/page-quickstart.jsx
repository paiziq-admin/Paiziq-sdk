/* ============================================================
   PAIZIQ DOCS — Quickstart page
   ============================================================ */
function PageQuickstart() {
  return (
    <>
      <div className="eyebrow">Getting started</div>
      <h1 className="page-title">Quickstart</h1>
      <p className="lead">
        Install the SDK, define a policy, and audit your first payment. The whole loop runs against
        the built-in mock gateway — no real money, no account required.
      </p>

      <H2 id="install" n="01">Install the SDK</H2>
      <p>The core package has zero runtime dependencies and supports Python 3.10+.</p>
      <CodeBlock tabs={[
        { label: "pip3", lang: "bash", code: `$ pip3 install paiziq` },
        { label: "redis extra", lang: "bash", code: `$ pip3 install "paiziq[redis]"` },
        { label: "postgres extra", lang: "bash", code: `$ pip3 install "paiziq[postgres]"` },
        { label: "dev", lang: "bash", code: `$ git clone <repo> && cd paiziq
$ make venv install   # python3 -m venv + editable install
$ make test` },
      ]} />

      <H2 id="init" n="02">Initialize the SDK</H2>
      <p>
        Create one <code>PaiziqSDK</code> and reuse it. The policy is plain data — thresholds,
        merchant lists, budgets, velocity limits.
      </p>
      <CodeBlock file="setup.py" lang="python" code={
`from paiziq import PaiziqSDK, PaymentPolicy

policy = PaymentPolicy(
    review_threshold=100.0,        # above this → needs_review
    hard_limit=1000.0,             # above this → rejected
    merchant_blocklist={"sketchy-vendor"},
    daily_budget=2_000.0,          # per-agent spend ceiling
    max_tx_per_hour=20,            # velocity guard
)

sdk = PaiziqSDK(
    policy=policy,
    api_key="pzq_…",                       # or PAIZIQ_API_KEY env var
    dashboard_endpoint="https://ingest.example.com",  # or PAIZIQ_ENDPOINT
)`} />
      <Callout type="warn">
        <b>Fail closed.</b> Payments flagged <code>needs_review</code> do not execute until a human
        calls <code>approve_review()</code>. Keep <code>require_review_approval=True</code> in
        production so nothing slips through unattended.
      </Callout>

      <H2 id="review" n="03">Review a payment</H2>
      <p>
        Wrap the intent your agent formed in a <code>PaymentRequest</code>. Review is side-effect
        free with respect to money.
      </p>
      <CodeBlock file="review.py" lang="python" code={
`from paiziq import PaymentRequest

request = PaymentRequest(
    agent_id="procurement-agent",
    principal_id="user-123",
    merchant="acme corp",
    amount=49.99,
    currency="USD",
    category="office-supplies",
    intent_description="Office supplies for Q3",
)

decision = sdk.review_payment(request)
print(decision.status.value)   # "approved" | "needs_review" | "rejected"
print(decision.reasons)        # human-readable explanations
print(decision.risk_flags)     # machine-readable flags`} />

      <H2 id="execute" n="04">Execute with the 4-way match</H2>
      <p>
        Execution re-verifies identity, intent, policy, and the transaction snapshot before charging
        the gateway. Branch on the decision — only approved payments move money.
      </p>
      <CodeBlock file="execute.py" lang="python" code={
`from paiziq import DecisionStatus

if decision.status is DecisionStatus.APPROVED:
    result = sdk.execute_payment(request)
elif decision.status is DecisionStatus.NEEDS_REVIEW:
    notify_reviewer(request, decision)       # a human resolves it
elif decision.status is DecisionStatus.REJECTED:
    log_blocked(request, decision.reasons)   # stop the agent

trail = sdk.get_audit_trail(request.request_id)
sdk.shutdown()   # flush exporters on exit`} />
      <p>
        That's the full loop. Next, set up <a className="link" href="#/authentication">keys and
        environments</a> and learn <a className="link" href="#/concepts">how a decision is built</a>.
      </p>
    </>
  );
}
Object.assign(window, { PageQuickstart });
