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
        <Param name="critical" type="severity" desc="Harmful intent was suspected" defaultOpen>
          <p className="pdetail">Fired when the decision carries <code>harmful_intent_suspected</code>. Page someone — the agent's stated intent matched harmful or evasive patterns.</p>
        </Param>
        <Param name="warning" type="severity" desc="A payment was rejected">
          <p className="pdetail">Fired for <code>rejected</code> decisions that do not carry the harmful-intent flag.</p>
        </Param>
        <Param name="info" type="severity" desc="A payment needs human review">
          <p className="pdetail">Fired for <code>needs_review</code>. Route to your review queue; execution is held until <code>approve_review()</code>. Approved decisions do not emit a notification.</p>
        </Param>
      </div>

      <H2 id="payload" n="02">Payload shape</H2>
      <p>Every notification shares one envelope, with machine-readable risk flags.</p>
      <CodeBlock file="notification.json" lang="json" code={
`{
  "severity": "info",
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
    -d '{"severity":"info","title":"Payment needs review",
         "message":"procurement-agent → acme corp",
         "request_id":"req-8f31"}'

# read them back
$ curl -H "Authorization: Bearer $PAIZIQ_API_KEY" \\
    "http://127.0.0.1:8800/v1/notifications"`} />

      <H2 id="reviewevents" n="05">Review workflow events</H2>
      <p>
        PZ-101 exposes a read-scoped <code>GET /v1/reviews</code> queue.
        Claim, release, reassign, request-more-info, escalate, approve, and decline
        mutations require a reviewer- or admin-capable key. These control-plane
        actions are separate from SDK notifications and enqueue outbound webhook
        events for matching endpoint subscriptions; each delivery is signed.
      </p>
      <CodeBlock file="review-event.json" lang="json" code={
`{
  "type": "review.approved",
  "created_at_ms": 1781074036632,
  "data": {
    "review_id": "rev_…",
    "payment_id": "pay_…",
    "reviewer_id": "alice",
    "state": "approved",
    "priority": "high",
    "last_action": "approved"
  }
}`} />
      <p>
        Event types are <code>review.assigned</code>, <code>review.claimed</code>,{" "}
        <code>review.released</code>, <code>review.reassigned</code>,{" "}
        <code>review.requested_info</code>, <code>review.escalated</code>,{" "}
        <code>review.approved</code>, <code>review.rejected</code>, and{" "}
        <code>review.sla_breached</code>. Deliveries carry a
        <code> Paiziq-Signature: t=...,v1=...</code> HMAC-SHA256 header and use
        exponential backoff before dead-lettering.
      </p>

      <H2 id="deliverylookup" n="06">Exact delivery correlation</H2>
      <p>
        <code>GET /v1/webhook-deliveries</code> applies
        <code> endpoint_id</code>, <code>state</code>, <code>env_id</code>,{" "}
        <code>event_type</code>, <code>payment_id</code>, and{" "}
        <code>review_id</code> on the server before pagination. Payment and
        review correlation compares the exact values in the event's{" "}
        <code>data</code> object; <code>meta.total</code> is the filtered count.
      </p>
      <CodeBlock file="deliveries.sh" lang="http" code={
`$ curl -H "Authorization: Bearer $PAIZIQ_API_KEY" \
    "http://127.0.0.1:8800/v1/webhook-deliveries?env_id=env_123&payment_id=pay_123&limit=200&offset=0"

# Review events use the same exact correlation path.
$ curl -H "Authorization: Bearer $PAIZIQ_API_KEY" \
    "http://127.0.0.1:8800/v1/webhook-deliveries?review_id=rev_123"`} />
      <p>
        Fetch <code>GET /v1/webhook-deliveries/&#123;delivery_id&#125;</code> to
        inspect that delivery's retry-attempt logs.
      </p>
    </>
  );
}
Object.assign(window, { PageWebhooks });
