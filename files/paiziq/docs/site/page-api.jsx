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
)`} />
      <div className="params">
        <Param name="review_payment(request)" type="→ Decision" required desc="Evaluate a PaymentRequest against the policy" defaultOpen>
          <p className="pdetail">Runs every rule, records an <code>AuditRecord</code>, emits a span, and fires notifiers. Pure read — never moves money.</p>
        </Param>
        <Param name="approve_review(request_id, approver)" type="→ Decision" desc="Human approval for a held payment">
          <p className="pdetail">Converts a <code>needs_review</code> decision to <code>approved</code> and records the override with the approver's identity.</p>
        </Param>
        <Param name="execute_payment(request)" type="→ ExecutionResult" desc="4-way match, then charge via the gateway">
          <p className="pdetail">Re-verifies identity, intent, policy, and transaction. On pass, calls the gateway and returns <code>executed=True</code> with a <code>gateway_reference</code>.</p>
        </Param>
        <Param name="get_audit_trail(request_id)" type="→ list[AuditRecord]" desc="Append-only history for one request" />
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
        <Param name="mandate" type="Mandate" desc="Optional signed authorization from the principal" />
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
decision.four_way_audit  # snapshot used by execute_payment()
decision.decided_at_ms   # epoch milliseconds`} />

      <H2 id="errors" n="05">Errors</H2>
      <div className="params">
        <Param name="PaymentBlockedError" type="exception" desc="Raised by enforcement wrappers on rejected payments" defaultOpen>
          <p className="pdetail">Raised by <code>guard_tool_call</code>, <code>instrument_payment_tool</code>, and the LangChain handler when <code>enforce=True</code>. Carries the <code>Decision</code> so callers can show reasons.</p>
        </Param>
      </div>
    </>
  );
}
Object.assign(window, { PageApi });
