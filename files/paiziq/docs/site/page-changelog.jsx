/* ============================================================
   PAIZIQ DOCS — Curated changelog highlights
   ============================================================ */
const CHANGELOG = [
  {
    version: "Unreleased", date: "",
    summary: "Control-plane workflows and developer experience highlights.",
    added: [
      ["Human-review workflow API (PZ-101)", "queue/detail/identity endpoints; key-name, tenant, and role binding; assignment and note-gated actions; open-review reuse and bypass guards; atomic payment resolution; migration 0008; audit entries; and signed review events."],
      ["Dashboard query contracts", "server-side payment currency/amount/text/time filtering and sort with exact totals; grouped risk flags and payments.total metrics; exact payment/review webhook correlation."],
      ["Policy draft audit reasons", "optional nonblank draft-update reasons are retained in append-only policy.draft_update audit detail."],
      ["Docs site", "static React docs site (docs/site/) implementing the Payment Agent SDK Guide design — animated audit pipeline, ⌘K palette, light/dark themes."],
      ["Project rules", "strict collaborator/agent rules in .cursor/rules/ and AGENTS.md."],
    ],
    changed: [],
  },
  {
    version: "0.2.0", date: "2026-06-09",
    summary: "Phase 1 hardening: production stores, PII scrubbing, ingest service, automation, and developer documentation.",
    added: [
      ["RedisBudgetStore", "shared, atomic spend ledger over Redis sorted sets for multi-process agent fleets."],
      ["PostgresAuditStore", "durable append-only audit trail over any DB-API 2.0 connection."],
      ["PIIScrubber + ScrubbingExporter", "redact emails, card numbers, SSNs, and configured keys from spans before export."],
      ["Ingest service", "FastAPI: POST /v1/traces (idempotent upsert), POST /v1/notifications, Bearer auth, SQLite storage."],
      ["Property-based tests", "Hypothesis suites for decision rules; concurrency tests for BudgetTracker and HTTPExporter."],
      ["Automation", "Makefile targets (make help) and GitHub Actions CI: lint, coverage on 3.10/3.12, examples, dist build."],
      ["Docs", "developer guide and human-readable progress tracker."],
    ],
    changed: [
      ["__version__", "bumped to 0.2.0; new classes re-exported from the top-level package."],
      ["pyproject.toml", "added redis and postgres extras; hypothesis in the dev extra."],
    ],
  },
  {
    version: "0.1.0", date: "2026-06-09",
    summary: "Phase 0 foundation (initial release).",
    added: [
      ["Core SDK", "zero-dependency models, decision engine with explainable rules, 4-way audit, policy and budget tracking."],
      ["Tracing", "Tracer, Span, ConsoleExporter, InMemoryExporter, HTTPExporter — stdlib only."],
      ["Stores & gateway", "in-memory and JSONL audit stores; MockGateway."],
      ["Integrations", "LangChain-style handler and OpenAI tool wrappers."],
      ["Quality", "44-test baseline suite and runnable examples."],
    ],
    changed: [],
  },
];

function PageChangelog() {
  return (
    <>
      <div className="eyebrow">Reference</div>
      <h1 className="page-title">Changelog</h1>
      <p className="lead">
        Curated release highlights are shown here. The complete canonical
        history lives in <code>CHANGELOG.md</code> (Keep a Changelog,
        semantic versioning).
      </p>
      {CHANGELOG.map((rel) => (
        <section key={rel.version}>
          <h2 id={"v" + rel.version.replace(/\./g, "")}>
            <span className="h2-num">{rel.date || "next"}</span>
            {rel.version === "Unreleased" ? rel.version : "v" + rel.version}
          </h2>
          <p>{rel.summary}</p>
          {rel.added.length > 0 && (
            <>
              <h3>Added</h3>
              <ul className="feat">
                {rel.added.map(([t, d]) => (
                  <li key={t}><strong>{t}.</strong> {d}</li>
                ))}
              </ul>
            </>
          )}
          {rel.changed.length > 0 && (
            <>
              <h3>Changed</h3>
              <ul className="feat">
                {rel.changed.map(([t, d]) => (
                  <li key={t}><strong>{t}.</strong> {d}</li>
                ))}
              </ul>
            </>
          )}
        </section>
      ))}
    </>
  );
}
Object.assign(window, { PageChangelog });
