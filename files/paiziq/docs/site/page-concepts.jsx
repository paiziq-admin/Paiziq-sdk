/* ============================================================
   PAIZIQ DOCS — How auditing works (concepts)
   ============================================================ */
function PageConcepts() {
  return (
    <>
      <div className="eyebrow">Guides</div>
      <h1 className="page-title">How auditing works</h1>
      <p className="lead">
        An audit is the complete story of one payment decision — a deterministic rule evaluation,
        a tamper check at execution time, and an immutable trail of everything in between.
      </p>

      <H2 id="model" n="01">Request → Decision → Audit</H2>
      <p>Three nouns carry the whole model:</p>
      <ul className="feat">
        <li><strong>PaymentRequest</strong> — what the agent wants to do. Agent, principal, merchant, amount, currency, category, and the stated intent.</li>
        <li><strong>Decision</strong> — the result of evaluating that request: <VChip kind="allow" />, <VChip kind="hold" />, or <VChip kind="block" />, with human-readable <code>reasons</code> and machine-readable <code>risk_flags</code>.</li>
        <li><strong>AuditRecord</strong> — an append-only event (<code>review</code>, <code>override</code>, <code>execution</code>) linked to the request and its trace.</li>
      </ul>

      <H2 id="rules" n="02">The decision engine</H2>
      <p>
        Rules run in order and are pure functions of the request and policy — no LLM, no randomness,
        no network. The strictest outcome wins: any <code>rejected</code> beats any
        <code> needs_review</code> beats <code>approved</code>.
      </p>
      <div className="card-grid">
        {[
          ["Amount", "hard_limit rejects; review_threshold escalates to a human."],
          ["Merchant", "Blocklist rejects; unknown merchants escalate (configurable)."],
          ["Budget", "Daily and monthly per-agent budgets, with a warning ratio."],
          ["Velocity", "max_tx_per_hour catches runaway loops."],
          ["Currency", "Only currencies you allow pass."],
          ["Category", "Sensitive categories always escalate for review."],
        ].map(([t, d]) => (
          <div className="card" key={t}>
            <Icon name="shield" className="card-ico" />
            <h4>{t}</h4>
            <p>{d}</p>
          </div>
        ))}
      </div>
      <Callout type="info">
        Determinism is what makes the audit trail trustworthy: re-running a decision with the same
        request and the same policy always produces the same verdict and the same reasons.
      </Callout>

      <H2 id="fourway" n="03">The 4-Way Match</H2>
      <p>
        Before money moves, <code>execute_payment()</code> re-verifies four dimensions against the
        snapshot taken at review time. Any mismatch refuses execution:
      </p>
      <ul className="feat">
        <li><strong>Identity</strong> — the same agent and principal that were reviewed.</li>
        <li><strong>Intent</strong> — the decision belongs to this exact request.</li>
        <li><strong>Policy</strong> — the decision status still permits execution.</li>
        <li><strong>Transaction</strong> — amount, currency, and merchant are unchanged since review.</li>
      </ul>
      <CodeBlock file="tamper.py" lang="python" code={
`decision = sdk.review_payment(request)

request.amount = 9_999.0   # agent (or attacker) mutates after review

result = sdk.execute_payment(request)
print(result.executed)     # False
print(result.error)        # "4-way audit failed: transaction"`} />

      <H2 id="spans" n="04">Spans &amp; the wire contract</H2>
      <p>
        Every SDK operation emits a <strong>span</strong> — a timestamped step with attributes and
        events. Exporters batch spans to your dashboard; the ingest service upserts them by
        <code> span_id</code>, so retries are idempotent.
      </p>
      <CodeBlock file="span.json" lang="json" code={
`{
  "name": "paiziq.execute_payment",
  "trace_id": "tr-7f3a",
  "span_id": "5e22be512e674c58",
  "parent_span_id": null,
  "start_ms": 1781074036632,
  "end_ms": 1781074036671,
  "duration_ms": 39,
  "status": "ok",
  "attributes": { "paiziq.decision": "approved", "paiziq.four_way_passed": true },
  "events": [
    { "name": "four_way_audit", "ts_ms": 1781074036670,
      "payload": { "checks": [{ "dim": "identity", "passed": true }] } }
  ]
}`} />
    </>
  );
}
Object.assign(window, { PageConcepts });
