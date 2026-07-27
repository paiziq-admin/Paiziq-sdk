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
        <b>API keys are bearer credentials.</b> Keep SDK/service keys server-side and never put them
        in notebook output or git history. The operator dashboard accepts a key in the browser only
        on trusted devices/origins and stores it in tab-scoped <code>sessionStorage</code>, clearing
        legacy <code>localStorage</code> credentials.
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
          <p className="pdetail">Server-side bootstrap keys. They resolve to full admin capability. A missing or malformed Bearer header returns <code>401</code>; an unknown key or insufficient capability returns <code>403</code>.</p>
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

      <H2 id="roles" n="04">Managed key roles</H2>
      <p>
        Database-backed keys are created, listed, rotated, and revoked through
        <code> /v1/api-keys</code>. Their persisted legacy scope remains{" "}
        <code>ingest</code>, <code>read</code>, or <code>admin</code>, while the
        role determines effective capabilities:
      </p>
      <ul className="feat">
        <li><strong>admin.</strong> Ingest, read, review, and admin mutations.</li>
        <li><strong>developer.</strong> Ingest and read.</li>
        <li><strong>reviewer.</strong> Read plus human-review mutations.</li>
        <li><strong>read_only.</strong> Read access only.</li>
      </ul>
      <Callout type="warn">
        Plaintext managed-key secrets are returned only by create and rotate
        responses. Store them immediately; list responses expose only a prefix.
      </Callout>

      <H2 id="reviewer-identity" n="05">Reviewer identity and tenant binding</H2>
      <p>
        <code>GET /v1/reviews/identity</code> reports the authenticated key's
        reviewer name, role, environment, and whether the identity is managed.
        Database-managed review reads are restricted to that environment.
      </p>
      <CodeBlock file="review-identity.json" lang="json" code={
`{
  "success": true,
  "data": {
    "reviewer_id": "payments-reviewer",
    "role": "reviewer",
    "env_id": "env_…",
    "managed_identity": true
  },
  "error": null
}`} />
      <p>
        Managed claim/release/request-info/escalation/approval/decline bodies
        must use the API-key name as <code>reviewer_id</code>. Reviewer and admin
        roles may mutate; developer and read-only roles cannot. Reassignment
        names the target reviewer, while the authenticated key remains the actor
        and non-admin callers must own the review. Bootstrap admins are unscoped
        and have no managed reviewer name.
      </p>

      <H2 id="secrets" n="06">Handling secrets</H2>
      <ul className="feat">
        <li><strong>One key per service.</strong> Give each deployment its own key so revocation is surgical.</li>
        <li><strong>Scrub before export.</strong> Wrap exporters in <code>ScrubbingExporter</code> so card numbers, SSNs, and emails never leave the process — see <a className="link" href="#/recipes~scrub">the PII recipe</a>.</li>
        <li><strong>Rotate by overlap.</strong> <code>PAIZIQ_INGEST_KEYS</code> accepts multiple bootstrap keys. Managed-key rotation supports a bounded grace period for the previous secret.</li>
      </ul>
    </>
  );
}
Object.assign(window, { PageAuth });
