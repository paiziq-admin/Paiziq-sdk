/* ============================================================
   PAIZIQ DOCS — Authentication page
   ============================================================ */
function PageAuth() {
  return (
    <>
      <div className="eyebrow">Getting started</div>
      <h1 className="page-title">Authentication</h1>
      <p className="lead">
        The SDK authenticates to your trace dashboard with a single API key. The ingest service
        validates the same key on every request. Keys live in the environment — never in code.
      </p>

      <H2 id="keys" n="01">SDK API key</H2>
      <p>
        Pass <code>api_key</code> to the constructor or set <code>PAIZIQ_API_KEY</code>. The key is
        sent as a Bearer token by the <code>HTTPExporter</code> with every trace batch.
      </p>
      <CodeBlock file="auth.py" lang="python" code={
`from paiziq import PaiziqSDK

# explicit
sdk = PaiziqSDK(api_key="pzq_live_…", dashboard_endpoint="https://ingest.example.com")

# or from the environment (preferred)
# PAIZIQ_API_KEY=pzq_live_…  PAIZIQ_ENDPOINT=https://ingest.example.com
sdk = PaiziqSDK()`} />
      <Callout type="danger">
        <b>API keys are bearer credentials.</b> Treat them like passwords — server-side only, never
        in a browser, notebook output, or git history. The repo rules forbid committing live keys.
      </Callout>

      <H2 id="envvars" n="02">Environment variables</H2>
      <div className="params">
        <Param name="PAIZIQ_API_KEY" type="env" desc="SDK key sent as Bearer token" defaultOpen>
          <p className="pdetail">Read at construction when <code>api_key</code> is not passed. Without a key and endpoint, spans fall back to the <code>ConsoleExporter</code>.</p>
        </Param>
        <Param name="PAIZIQ_ENDPOINT" type="env" desc="Trace dashboard / ingest base URL">
          <p className="pdetail">When set, the SDK wires an <code>HTTPExporter</code> that batches spans to <code>POST /v1/traces</code>.</p>
        </Param>
        <Param name="PAIZIQ_INGEST_KEYS" type="env" desc="Comma-separated keys the ingest service accepts">
          <p className="pdetail">Server-side counterpart. Requests with other keys get <code>401 invalid API key</code>.</p>
        </Param>
        <Param name="PAIZIQ_INGEST_DB" type="env" desc="SQLite path for the ingest service (default in-memory)" />
      </div>

      <H2 id="ingest" n="03">Ingest service keys</H2>
      <p>
        The bundled FastAPI ingest service authenticates every endpoint except <code>/health</code>.
        Configure accepted keys at deploy time:
      </p>
      <CodeBlock tabs={[
        { label: "run.sh", lang: "bash", code:
`$ export PAIZIQ_INGEST_KEYS="pzq_live_key1,pzq_svc_key2"
$ make ingest-run     # uvicorn app:app on :8800` },
        { label: "request.sh", lang: "http", code:
`$ curl -X POST http://127.0.0.1:8800/v1/traces \\
    -H "Authorization: Bearer pzq_live_key1" \\
    -H "Content-Type: application/json" \\
    -d '{"spans": []}'` },
      ]} />

      <H2 id="secrets" n="04">Handling secrets</H2>
      <ul className="feat">
        <li><strong>One key per service.</strong> Give each deployment its own key so revocation is surgical.</li>
        <li><strong>Scrub before export.</strong> Wrap exporters in <code>ScrubbingExporter</code> so card numbers, SSNs, and emails never leave the process — see <a className="link" href="#/recipes~scrub">the PII recipe</a>.</li>
        <li><strong>Rotate by overlap.</strong> <code>PAIZIQ_INGEST_KEYS</code> accepts multiple keys: add the new one, roll deployments, then remove the old.</li>
      </ul>
    </>
  );
}
Object.assign(window, { PageAuth });
