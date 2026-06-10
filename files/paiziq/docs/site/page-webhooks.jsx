/* ============================================================
   PAIZIQ DOCS — Notifications & events
   ============================================================ */
function PageWebhooks() {
  return (
    <>
      <div className="eyebrow">Guides</div>
      <h1 className="page-title">Notifications &amp; events</h1>
      <p className="lead">
        Every decision can fan out to notifiers — console, webhook, or your own. Use them to page
        on-call, fill a review queue, or mirror events into your warehouse.
      </p>

      <H2 id="types" n="01">Notification severities</H2>
      <div className="params">
        <Param name="critical" type="severity" desc="A payment was rejected" defaultOpen>
          <p className="pdetail">Fired for <code>rejected</code> decisions. Page someone — the agent attempted something policy forbids.</p>
        </Param>
        <Param name="warning" type="severity" desc="A payment needs human review">
          <p className="pdetail">Fired for <code>needs_review</code>. Route to your review queue; execution is held until <code>approve_review()</code>.</p>
        </Param>
        <Param name="info" type="severity" desc="Routine decision activity">
          <p className="pdetail">Approved payments and other low-urgency events.</p>
        </Param>
      </div>

      <H2 id="payload" n="02">Payload shape</H2>
      <p>Every notification shares one envelope, with machine-readable risk flags.</p>
      <CodeBlock file="notification.json" lang="json" code={
`{
  "severity": "warning",
  "title": "Payment needs review",
  "message": "procurement-agent → acme corp for $249.99",
  "request_id": "req-8f31…",
  "risk_flags": ["amount_above_review_threshold", "unknown_merchant"],
  "created_at_ms": 1781074036632
}`} />

      <H2 id="notifier" n="03">Custom notifiers</H2>
      <p>
        A notifier is anything with a <code>send(notification)</code> method. Wire them at
        construction; failures are logged and never break the payment path.
      </p>
      <CodeBlock tabs={[
        { label: "webhook.py", lang: "python", code:
`from paiziq import PaiziqSDK
from paiziq.notifications import WebhookNotifier, ConsoleNotifier

sdk = PaiziqSDK(
    notifiers=[
        ConsoleNotifier(),
        WebhookNotifier("https://hooks.example.com/paiziq"),
    ],
)` },
        { label: "custom.py", lang: "python", code:
`class SlackNotifier:
    def __init__(self, channel: str):
        self.channel = channel

    def send(self, notification) -> None:
        if notification.severity == "critical":
            post_to_slack(self.channel, notification.title,
                          notification.message)

sdk = PaiziqSDK(notifiers=[SlackNotifier("#payments-oncall")])` },
      ]} />
      <Callout type="warn">
        <b>Observability never breaks the agent.</b> Notifier and exporter exceptions are caught and
        logged inside the SDK. A dead webhook endpoint cannot stop a legitimate payment.
      </Callout>

      <H2 id="ingestapi" n="04">Ingest endpoints</H2>
      <p>
        The bundled ingest service accepts notifications alongside traces, so one endpoint can feed
        a dashboard and an alert stream.
      </p>
      <CodeBlock file="notify.sh" lang="http" code={
`$ curl -X POST http://127.0.0.1:8800/v1/notifications \\
    -H "Authorization: Bearer $PAIZIQ_API_KEY" \\
    -H "Content-Type: application/json" \\
    -d '{"severity":"warning","title":"Payment needs review",
         "message":"procurement-agent → acme corp",
         "request_id":"req-8f31"}'

# read them back
$ curl -H "Authorization: Bearer $PAIZIQ_API_KEY" \\
    "http://127.0.0.1:8800/v1/notifications?severity=warning"`} />
    </>
  );
}
Object.assign(window, { PageWebhooks });
