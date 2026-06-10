/* ============================================================
   PAIZIQ DOCS — Recipes
   ============================================================ */
function PageRecipes() {
  return (
    <>
      <div className="eyebrow">Guides</div>
      <h1 className="page-title">Recipes</h1>
      <p className="lead">
        Small, production-ready patterns: enforce at the tool boundary, scrub PII before export,
        and share budgets across replicas.
      </p>

      <H2 id="toolguard" n="01">Guard a framework tool</H2>
      <p>
        The safest place to enforce is the tool boundary — the moment an agent framework is about to
        invoke a payment tool. Wrap the tool so every call is reviewed first.
      </p>
      <CodeBlock tabs={[
        { label: "decorator.py", lang: "python", code:
`from paiziq import PaiziqSDK, PaymentRequest, instrument_payment_tool

sdk = PaiziqSDK()

def extract(merchant: str, amount: float) -> PaymentRequest:
    return PaymentRequest(
        agent_id="procurement-agent", principal_id="user-123",
        merchant=merchant, amount=amount,
        intent_description="tool call",
    )

@instrument_payment_tool(sdk, extract, enforce=True)
def pay(merchant: str, amount: float):
    return charge_card(merchant, amount)

pay("acme corp", 49.99)        # reviewed, then runs
pay("sketchy llc", 99_999.0)   # raises PaymentBlockedError` },
        { label: "langchain.py", lang: "python", code:
`from paiziq import PaiziqSDK, create_langchain_handler

sdk = PaiziqSDK()
handler = create_langchain_handler(
    sdk,
    payment_tools={"make_payment", "send_wire"},
    agent_id="procurement-agent",
    principal_id="user-123",
)

agent_executor.invoke(
    {"input": "buy supplies"},
    config={"callbacks": [handler]},
)` },
        { label: "openai.py", lang: "python", code:
`from paiziq import PaiziqSDK, guard_tool_call

sdk = PaiziqSDK()

for call in response.choices[0].message.tool_calls or []:
    guard_tool_call(            # raises PaymentBlockedError on reject
        sdk, call.function.name, call.function.arguments,
        payment_tools={"make_payment"},
        agent_id="assistant-1", principal_id="user-123",
    )
    dispatch(call)` },
      ]} />

      <H2 id="scrub" n="02">Scrub PII before export</H2>
      <p>
        Wrap any exporter in <code>ScrubbingExporter</code>. The default <code>PIIScrubber</code>
        redacts card numbers, SSNs, and emails from span attributes and events in-process — the raw
        values never reach the network.
      </p>
      <CodeBlock file="scrub.py" lang="python" code={
`from paiziq import PaiziqSDK, ScrubbingExporter, PIIScrubber
from paiziq.tracing import HTTPExporter

exporter = ScrubbingExporter(
    inner=HTTPExporter("https://ingest.example.com", api_key="pzq_live_…"),
    scrubber=PIIScrubber(redact_keys={"card_number", "ssn"}),
)
sdk = PaiziqSDK(exporters=[exporter])`} />

      <H2 id="redis" n="03">Share budgets across replicas</H2>
      <p>
        The in-memory budget tracker is per-process. With several agent replicas, point them at one
        Redis so daily and monthly budgets are enforced globally.
      </p>
      <CodeBlock file="redis.sh" lang="bash" code={
`$ pip3 install "paiziq[redis]"`} />
      <CodeBlock file="shared_budget.py" lang="python" code={
`from paiziq import BudgetTracker, PaiziqSDK, PaymentPolicy, RedisBudgetStore

sdk = PaiziqSDK(
    policy=PaymentPolicy(daily_budget=500.0),
    budget_tracker=BudgetTracker(
        store=RedisBudgetStore(url="redis://localhost:6379/0"),
    ),
)`} />
      <Callout type="info">
        Same pattern for durable audits: <code>pip3 install "paiziq[postgres]"</code> and pass
        <code> PostgresAuditStore(dsn=…)</code> so the audit trail survives restarts.
      </Callout>
    </>
  );
}
Object.assign(window, { PageRecipes });
