/* ============================================================
   PAIZIQ DOCS — API reference
   ============================================================ */
function PageApi() {
  return (
    <>
      <div className="eyebrow">Reference</div>
      <h1 className="page-title">API reference</h1>
      <p className="lead">
        The complete public surface of <code>paiziq</code>. Everything here is re-exported from the
        top-level package and covered by the test suite.
      </p>

      <H2 id="sdk" n="01">PaiziqSDK</H2>
      <CodeBlock file="constructor.py" lang="python" code={
`PaiziqSDK(
    policy=None,                  # PaymentPolicy — rules to enforce
    api_key=None,                 # or PAIZIQ_API_KEY
    dashboard_endpoint=None,      # or PAIZIQ_ENDPOINT
    gateway=None,                 # PaymentGateway (default MockGateway)
    audit_store=None,             # AuditStore (default in-memory)
    notifiers=None,               # list[Notifier]
    exporters=None,               # list[Exporter]
    budget_tracker=None,          # BudgetTracker
    service_name="payment-agent",
    require_review_approval=True, # fail closed on needs_review
    failure_mode=FailureMode.FAIL_CLOSED,
    execution_ledger=None,       # default: in-memory SQLiteExecutionLedger
    org_id="local", env_id="local",
    policy_version="local",
    clock=None,                  # optional epoch-millisecond clock
    review_ttl_ms=900_000,        # 15-minute local approval lifetime
    evidence_ledger=None,        # optional local review evidence store
)`} />
      <div className="params">
        <Param name="review_payment(request)" type="→ Decision" required desc="Evaluate a PaymentRequest against the policy" defaultOpen>
          <p className="pdetail">Runs every rule, records an <code>AuditRecord</code>, emits a span, and fires notifiers. Pure read — never moves money.</p>
        </Param>
        <Param name="approve_review(request_id, reviewer_id)" type="→ None" desc="Human approval for the current local review">
          <p className="pdetail">Appends an approval for one unexpired request and policy revision. The stored decision remains immutable. Hosted holds require the service review API.</p>
        </Param>
        <Param name="execute_payment(request)" type="→ ExecutionResult" desc="4-way match, then charge via the gateway">
          <p className="pdetail">Rechecks request, policy, mandate and current history, then reserves capacity before calling the gateway. Repeated IDs return their original execution. Unknown results keep their reservation. A confirmed provider response survives a failed ledger write as <code>accounting_pending=True</code>.</p>
        </Param>
        <Param name="reconcile_payment(request_id, *, outcome=None)" type="→ ExecutionResult" desc="Resolve a receipt without charging">
          <p className="pdetail">Queries <code>gateway.lookup(key)</code>, or accepts a trusted <code>GatewayOutcome</code>. Unknown evidence keeps exposure held. Hosted reconciliation requires admin access and evidence.</p>
        </Param>
        <Param name="get_execution_events(request_id=None, limit=100)" type="→ list[dict]" desc="Local durable business evidence" />
        <Param name="get_audit_trail(request_id=None, limit=100)" type="→ list[dict]" desc="Optional append-only audit projection" />
        <Param name="shutdown()" type="→ None" desc="Flush exporters and release resources" />
      </div>

      <H2 id="request" n="02">PaymentRequest</H2>
      <div className="params">
        <Param name="agent_id" type="str" required desc="The agent making the payment" defaultOpen />
        <Param name="principal_id" type="str" required desc="The human or org the agent acts for" />
        <Param name="merchant" type="str" required desc="Payee, normalized lowercase" />
        <Param name="amount" type="float" required desc="Transaction amount" />
        <Param name="currency" type="str" desc='ISO 4217 code, default "USD"' />
        <Param name="category" type="str" desc="Spend category for policy rules" />
        <Param name="intent_description" type="str" desc="The agent's stated reason — auditable" />
        <Param name="mandate" type="Mandate" desc="Optional principal authorization data; signature verification is not implemented" />
        <Param name="metadata" type="dict" desc="Free-form context attached to the audit trail" />
      </div>

      <H2 id="policy" n="03">PaymentPolicy</H2>
      <div className="params">
        <Param name="review_threshold" type="float" desc="Amounts above this escalate to a human" defaultOpen />
        <Param name="hard_limit" type="float" desc="Amounts above this are rejected outright" />
        <Param name="merchant_allowlist / merchant_blocklist" type="set[str]" desc="Explicit allow / deny lists" />
        <Param name="treat_unknown_merchant_as" type="str" desc='What to do with unrecognized merchants (default "needs_review")' />
        <Param name="daily_budget / monthly_budget" type="float" desc="Per-agent spend ceilings" />
        <Param name="budget_warning_ratio" type="float" desc="Risk-flag threshold as a fraction of budget" />
        <Param name="review_categories" type="set[str]" desc="Categories that always need review" />
        <Param name="allowed_currencies" type="set[str]" desc="Currencies that pass the currency rule" />
        <Param name="max_tx_per_hour" type="int" desc="Velocity limit per agent" />
      </div>

      <H2 id="decision" n="04">Decision</H2>
      <CodeBlock file="decision.py" lang="python" code={
`decision.request_id      # "req-8f31…"
decision.status          # DecisionStatus.APPROVED | NEEDS_REVIEW | REJECTED
decision.reasons         # ["amount $49.99 within review threshold", …]
decision.risk_flags      # [RiskFlag.UNKNOWN_MERCHANT, …]
decision.rule_results    # per-rule verdicts, in evaluation order
decision.four_way_audit  # optional field; execution does not mutate this review
decision.decided_at_ms   # epoch milliseconds`} />

      <H2 id="errors" n="05">Errors</H2>
      <div className="params">
        <Param name="PaymentBlockedError" type="exception" desc="Raised by enforcement wrappers on rejected payments" defaultOpen>
          <p className="pdetail">Raised by <code>guard_tool_call</code>, <code>instrument_payment_tool</code>, and the LangChain handler when <code>enforce=True</code>. Carries the <code>Decision</code> so callers can show reasons.</p>
        </Param>
      </div>

      <H2 id="execution" n="06">Execution authority</H2>
      <p>
        In 0.3.0, <code>SQLiteExecutionLedger("payments.sqlite")</code> shares durable claims
        across local workers. The default in-memory ledger lasts for one process lifetime.
        <code> HostedExecutionLedger(transport, org_id=..., env_id=...)</code> selects the
        server policy and budget authority explicitly. A trace endpoint does not select it.
        Budgets are scoped by organization, environment, agent and currency. No FX conversion occurs.
      </p>
      <p>
        <code>ExecutionResult.status</code> is blocked, reserved, submitted, confirmed, failed,
        or unknown. It also exposes <code>execution_id</code>, <code>provider_idempotency_key</code>,
        <code> replayed</code> and <code>accounting_pending</code>. Keep one request ID per logical
        action. Do not use a new ID to retry an unknown charge.
      </p>
      <p>
        Optional gateway methods <code>charge_idempotent(request, key)</code> and
        <code> lookup(key)</code> support provider deduplication and receipt recovery.
        <code> GatewayOutcome</code> carries confirmed, failed or unknown evidence.
        <code> GatewayDeclined</code> means the provider confirms no side effect.
        Other provider exceptions are unknown. Legacy gateways cannot promise automatic recovery.
      </p>
      <p>
        Hosted evidence is available at <code>GET /v1/payments/&#123;payment_id&#125;/execution</code>.
        The claim, report and reconcile POST routes extend that path. State changes and business
        events commit together; webhook consumers must deduplicate stable event IDs.
      </p>
    </>
  );
}
Object.assign(window, { PageApi });
