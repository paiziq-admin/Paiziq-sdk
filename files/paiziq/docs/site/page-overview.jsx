/* ============================================================
   PAIZIQ DOCS — Overview page
   ============================================================ */
function H2({ id, n, children }) {
  return <h2 id={id}><span className="h2-num">{n}</span>{children}</h2>;
}

function PageOverview() {
  return (
    <>
      <div className="eyebrow">Documentation</div>
      <h1 className="page-title">Audit every payment<br />your agents make.</h1>
      <p className="lead">
        Paiziq wraps autonomous payment agents. Every payment request passes through a deterministic
        decision engine and a 4-Way Match before money moves. You get an immutable, traceable record
        of <em>why</em> each payment was approved, held, or rejected.
      </p>

      <TraceGraph />

      <H2 id="what" n="01">What it does</H2>
      <p>
        Agents act faster than humans can review. Paiziq sits between your agent and your payment
        gateway as a <strong>policy checkpoint</strong>: it evaluates each proposed payment against
        your <code>PaymentPolicy</code>, verifies the executed transaction matches what was reviewed,
        and appends a signed audit record — with zero runtime dependencies.
      </p>
      <div style={{ display: "flex", gap: 10, margin: "20px 0", flexWrap: "wrap" }}>
        <VChip kind="allow" /><span style={{ color: "var(--text-faint)", fontSize: 13, alignSelf: "center" }}>execute it</span>
        <VChip kind="hold" /><span style={{ color: "var(--text-faint)", fontSize: 13, alignSelf: "center" }}>queue for a human</span>
        <VChip kind="block" /><span style={{ color: "var(--text-faint)", fontSize: 13, alignSelf: "center" }}>stop the agent</span>
      </div>

      <H2 id="pipeline" n="02">The audit pipeline</H2>
      <p>Every payment fans out through five stages. Each leaves a span you can inspect later.</p>
      <div className="card-grid">
        {[
          ["Agent", "The intent is captured with full context — agent, principal, merchant, amount."],
          ["Rules", "The decision engine runs your policy rules. Deterministic and explainable."],
          ["4-Way Match", "Identity, intent, policy, and transaction are re-verified at execution time."],
          ["Gateway", "The charge executes only when every check passes."],
          ["Audit", "An immutable record is appended and traced to your dashboard."],
        ].map(([t, d], i) => (
          <div className="card" key={t}>
            <span className="card-num">{String(i + 1).padStart(2, "0")}</span>
            <Icon name={["node", "shield", "layers", "zap", "check"][i]} className="card-ico" />
            <h4>{t}</h4>
            <p>{d}</p>
          </div>
        ))}
      </div>

      <div className="stat-strip">
        <div className="stat"><div className="stat-val">0<span className="u">deps</span></div><div className="stat-lbl">stdlib-only core — optional extras</div></div>
        <div className="stat"><div className="stat-val">100<span className="u">%</span></div><div className="stat-lbl">payments with an audit record</div></div>
        <div className="stat"><div className="stat-val">75<span className="u">tests</span></div><div className="stat-lbl">unit, property-based, and e2e</div></div>
      </div>

      <H2 id="why" n="03">Why teams use it</H2>
      <ul className="feat">
        <li><strong>Deterministic guardrails.</strong> No LLM in the decision path — the same request and policy always yield the same decision, with machine-readable reasons.</li>
        <li><strong>An audit trail by default.</strong> Every review, override, and execution is appended to an immutable store, not a log line you hope was written.</li>
        <li><strong>A 4-Way Match that means it.</strong> If the transaction drifts from what was reviewed — amount, merchant, identity, policy — execution is refused.</li>
        <li><strong>Drop-in.</strong> One wrap around your existing payment call. LangChain, OpenAI tool-call, and decorator integrations included.</li>
      </ul>

      <H2 id="first" n="04">First audit in 60 seconds</H2>
      <CodeBlock
        tabs={[
          {
            label: "audit.py", lang: "python",
            code:
`from paiziq import PaiziqSDK, PaymentRequest, PaymentPolicy

sdk = PaiziqSDK(policy=PaymentPolicy(review_threshold=100.0, hard_limit=1000.0))

request = PaymentRequest(
    agent_id="procurement-agent",
    principal_id="user-123",
    merchant="acme corp",
    amount=49.99,
    intent_description="Office supplies for Q3",
)

decision = sdk.review_payment(request)
print(decision.status)        # DecisionStatus.APPROVED

result = sdk.execute_payment(request)
print(result.executed, result.gateway_reference)`,
          },
          {
            label: "install.sh", lang: "bash",
            code:
`$ pip3 install paiziq

# optional extras
$ pip3 install "paiziq[redis]"      # shared budget store
$ pip3 install "paiziq[postgres]"   # durable audit store
$ pip3 install "paiziq[langchain]"  # framework integration`,
          },
        ]}
      />
      <Callout type="info">
        New here? Follow the <a className="link" href="#/quickstart">Quickstart</a> next — it walks
        through install, policy setup, and acting on a decision.
      </Callout>
    </>
  );
}
Object.assign(window, { H2, PageOverview });
