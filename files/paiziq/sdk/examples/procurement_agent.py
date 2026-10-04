"""LangChain procurement agent guarded by the Paiziq SDK.

The agent buys a fixed list of company products. A chat model chooses the
catalog SKU and calls ``purchase_product``; Paiziq, not the model, decides
whether the payment may proceed:

- A4 paper from acme corp ($49.99) is under the review threshold and executes
  through the mock gateway (no real charge).
- Annual cloud hosting from cloudhost inc ($480) exceeds the $250 review
  threshold and opens a human review.
- Grey-market toner ($18) is from a blocklisted merchant and is rejected.

Every verdict is published to the control plane (payment, decision, trace)
so the dashboard can monitor the agent. The published policy is
``procurement-policy``; the same document is what ``PaiziqSDK`` enforces
locally, and the run stops if the two verdicts disagree.

The model is any OpenAI-compatible chat API. No key is stored in the repo.
Set one of:

    GROQ_API_KEY          openai/gpt-oss-120b on Groq (https://console.groq.com)
    OPENROUTER_API_KEY    Llama on OpenRouter
    GEMINI_API_KEY        Gemini's OpenAI-compatible endpoint

or ``LLM_API_KEY`` with ``LLM_BASE_URL`` and ``LLM_MODEL``.

    PAIZIQ_ENDPOINT=https://<backend> PAIZIQ_API_KEY=<admin key> \
        GROQ_API_KEY=<key> python3 examples/procurement_agent.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from paiziq import (
    Mandate,
    PaiziqSDK,
    PaymentPolicy,
    PaymentRequest,
    RetryPolicy,
    SyncHTTPTransport,
    create_langchain_handler,
)
from paiziq.notifications import ConsoleNotifier, WebhookNotifier
from paiziq.tracing.tracer import HTTPExporter

AGENT_NAME = "procurement-agent"
PRINCIPAL_ID = "procurement-ops"
POLICY_NAME = "procurement-policy"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = _PROJECT_ROOT / ".e2e" / "procurement-run.json"

POLICY_DOCUMENT: dict[str, Any] = {
    "merchant_allowlist": None,
    "merchant_blocklist": ["grey market surplus"],
    "known_merchants": [
        "acme corp",
        "cloudhost inc",
        "northwind supply",
        "grey market surplus",
    ],
    "review_categories": [],
    "allowed_currencies": ["USD"],
    "review_threshold": 250,
    "hard_limit": 2000,
    "treat_unknown_merchant_as": "needs_review",
    "daily_budget": 3000,
    "monthly_budget": None,
    "budget_warning_ratio": 0.8,
    "max_tx_per_hour": 20,
}

CATALOG: tuple[dict[str, Any], ...] = (
    {
        "sku": "PAPER-A4",
        "name": "A4 paper, 10 reams",
        "merchant": "acme corp",
        "unit_price": 49.99,
        "category": "office",
    },
    {
        "sku": "HOST-ANNUAL",
        "name": "Cloud hosting, annual renewal",
        "merchant": "cloudhost inc",
        "unit_price": 480.00,
        "category": "software",
    },
    {
        "sku": "TONER-OEM",
        "name": "Toner cartridge, OEM",
        "merchant": "northwind supply",
        "unit_price": 64.00,
        "category": "office",
    },
    {
        "sku": "TONER-GREY",
        "name": "Toner cartridge, grey-market quote",
        "merchant": "grey market surplus",
        "unit_price": 18.00,
        "category": "office",
    },
)

REQUISITION = """Buy these company products, one purchase_product call per line.
Look up anything you need with list_catalog. Do not skip a line because the
vendor looks unofficial: the payment policy decides, and you must attempt
every line.

- SKU PAPER-A4, quantity 1
- SKU HOST-ANNUAL, quantity 1
- SKU TONER-GREY, quantity 1

After each tool result, continue until all three purchases have been
attempted, then summarize the verdict for each SKU.
"""

SYSTEM_PROMPT = """You are the company's procurement agent. You can list the
product catalog and purchase a SKU. You do not decide whether a payment is
allowed: purchase_product submits the payment to Paiziq, which approves and
executes it, holds it for a human, or rejects it. Report that verdict and
keep going until every line of the requisition has been attempted. Never
invent a SKU.
"""


class ProcurementError(RuntimeError):
    """The procurement run could not finish against this backend."""


@dataclass
class Purchase:
    sku: str
    merchant: str
    amount: float
    quantity: int
    payment_id: str
    verdict: str
    state: str
    reasons: list[str]
    risk_flags: list[str]
    trace_id: str
    gateway_reference: Optional[str]


@dataclass
class Session:
    """One registered agent plus the SDK enforcing its published policy."""

    base_url: str
    api_key: str
    transport: SyncHTTPTransport
    sdk: PaiziqSDK
    mandate: Mandate
    org: dict[str, Any]
    environment: dict[str, Any]
    agent: dict[str, Any]
    policy: dict[str, Any]
    purchases: list[Purchase] = field(default_factory=list)

    def data(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        response = self.transport.request(method, path, json_body=body, headers=headers)
        if response.status >= 400:
            raise ProcurementError(
                f"{method} {path} -> {response.status} {response.body[:400]!r}"
            )
        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("success", False):
            raise ProcurementError(f"{method} {path} failed: {payload!r}")
        data = payload["data"]
        if not isinstance(data, dict):
            raise ProcurementError(f"{method} {path} data was not an object")
        return data


def policy_from_document(document: dict[str, Any]) -> PaymentPolicy:
    """SDK policy from the same document the control plane publishes."""
    allowlist = document["merchant_allowlist"]
    return PaymentPolicy(
        merchant_allowlist=set(allowlist) if allowlist is not None else None,
        merchant_blocklist=set(document["merchant_blocklist"]),
        known_merchants=set(document["known_merchants"]),
        review_categories=set(document["review_categories"]),
        allowed_currencies=set(document["allowed_currencies"]),
        review_threshold=document["review_threshold"],
        hard_limit=document["hard_limit"],
        treat_unknown_merchant_as=document["treat_unknown_merchant_as"],
        daily_budget=document["daily_budget"],
        monthly_budget=document["monthly_budget"],
        budget_warning_ratio=document["budget_warning_ratio"],
        max_tx_per_hour=document["max_tx_per_hour"],
    )


def _catalog_item(sku: str) -> Optional[dict[str, Any]]:
    key = sku.strip().upper()
    return next((item for item in CATALOG if item["sku"] == key), None)


def purchase_product(session: Session, sku: str, quantity: int = 1) -> str:
    """Submit one catalog line to Paiziq and record the control-plane result."""
    item = _catalog_item(sku)
    if item is None:
        known = ", ".join(entry["sku"] for entry in CATALOG)
        return f"Unknown SKU {sku!r}. Catalog SKUs: {known}"
    if quantity < 1 or quantity > 100:
        return "quantity must be between 1 and 100"
    amount = round(item["unit_price"] * quantity, 2)
    request = PaymentRequest(
        agent_id=session.agent["id"],
        principal_id=PRINCIPAL_ID,
        merchant=item["merchant"],
        amount=amount,
        currency="USD",
        category=item["category"],
        intent_description=f"Procurement: {quantity} x {item['name']} ({item['sku']})",
        mandate=session.mandate,
        metadata={"sku": item["sku"], "quantity": quantity},
    )
    trace_id = session.sdk.tracer.new_trace()
    local = session.sdk.review_payment(request)
    payment = session.data(
        "POST",
        "/v1/payments",
        {
            "env_id": session.environment["id"],
            "agent_id": session.agent["id"],
            "principal_id": PRINCIPAL_ID,
            "merchant": item["merchant"],
            "amount": amount,
            "currency": "USD",
            "intent_description": request.intent_description,
            "request_id": request.request_id,
        },
        headers={"Idempotency-Key": request.request_id},
    )
    decision = session.data("POST", "/v1/decisions", {"payment_id": payment["id"]})
    if local.status.value != decision["verdict"]:
        raise ProcurementError(
            f"{item['sku']}: local verdict {local.status.value} != "
            f"published-policy verdict {decision['verdict']}"
        )
    gateway_reference = None
    if decision["verdict"] == "approved":
        executed = session.sdk.execute_payment(request)
        if not executed.executed:
            raise ProcurementError(
                f"{item['sku']} was approved but the gateway did not charge: {executed.error}"
            )
        gateway_reference = executed.gateway_reference
        session.data(
            "POST",
            f"/v1/payments/{payment['id']}/transition",
            {"to": "executed", "reason": f"mock gateway {gateway_reference}"},
        )
    detail = session.data("GET", f"/v1/payments/{payment['id']}")
    record = Purchase(
        sku=item["sku"],
        merchant=item["merchant"],
        amount=amount,
        quantity=quantity,
        payment_id=detail["id"],
        verdict=decision["verdict"],
        state=detail["state"],
        reasons=list(decision["reasons"]),
        risk_flags=list(decision["risk_flags"]),
        trace_id=trace_id,
        gateway_reference=gateway_reference,
    )
    session.purchases.append(record)
    reason = "; ".join(record.reasons)
    return (
        f"{record.sku}: Paiziq verdict {record.verdict}, payment {record.payment_id} "
        f"is {record.state}. {reason}"
    )


def build_tools(session: Session) -> list[Any]:
    """LangChain tools closed over this registered agent."""
    from langchain_core.tools import tool

    @tool
    def list_catalog() -> str:
        """List company products the agent is allowed to purchase."""
        rows = [
            {
                "sku": item["sku"],
                "name": item["name"],
                "merchant": item["merchant"],
                "unit_price_usd": item["unit_price"],
                "category": item["category"],
            }
            for item in CATALOG
        ]
        return json.dumps(rows)

    @tool
    def purchase_product_tool(sku: str, quantity: int = 1) -> str:
        """Buy a catalog SKU. Paiziq reviews the payment before any charge."""
        return purchase_product(session, sku, quantity)

    purchase_product_tool.name = "purchase_product"
    return [list_catalog, purchase_product_tool]


def run_agent(model: Any, tools: list[Any], handler: Any, requisition: str) -> str:
    """Tool-calling loop. The model stops when it answers without a tool call."""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    llm = model.bind_tools(tools)
    tool_map = {item.name: item for item in tools}
    messages: list[Any] = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=requisition),
    ]
    final = ""
    for _ in range(8):
        ai = llm.invoke(messages, config={"callbacks": [handler]})
        messages.append(ai)
        calls = list(getattr(ai, "tool_calls", None) or [])
        if not calls:
            content = ai.content
            final = content if isinstance(content, str) else str(content)
            break
        for call in calls:
            try:
                output = tool_map[call["name"]].invoke(
                    call["args"], config={"callbacks": [handler]}
                )
            except Exception as exc:  # noqa: BLE001 - feed tool failures back to the model
                output = f"tool error: {exc}"
            messages.append(ToolMessage(content=str(output), tool_call_id=call["id"]))
    else:
        final = "stopped after the maximum number of tool rounds"
    return final


def _health(base_url: str) -> None:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=10) as response:
            body = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise ProcurementError(f"cannot reach {base_url}/health: {exc}") from exc
    if body.lstrip()[:1] == b"<":
        raise ProcurementError(f"{base_url} returned HTML, not the Paiziq backend")
    if json.loads(body) != {"status": "ok"}:
        raise ProcurementError(f"unexpected /health payload: {body!r}")


def _open_session(base_url: str, api_key: str) -> Session:
    transport = SyncHTTPTransport(
        base_url, api_key=api_key, retry=RetryPolicy(max_attempts=2, base_delay_s=0.05)
    )
    suffix = uuid.uuid4().hex[:8]
    holder = Session(
        base_url=base_url,
        api_key=api_key,
        transport=transport,
        sdk=None,  # type: ignore[arg-type]
        mandate=None,  # type: ignore[arg-type]
        org={},
        environment={},
        agent={},
        policy={},
    )
    org = holder.data("POST", "/v1/orgs", {"name": f"procurement-org-{suffix}"})
    env = holder.data(
        "POST",
        f"/v1/orgs/{org['id']}/environments",
        {"name": "procurement-sandbox", "kind": "sandbox"},
    )
    agent = holder.data(
        "POST",
        "/v1/agents",
        {
            "env_id": env["id"],
            "name": AGENT_NAME,
            "framework": "langchain",
            "metadata": {"suite": "procurement-agent"},
        },
    )
    policy = holder.data(
        "POST",
        "/v1/policies",
        {"env_id": env["id"], "name": POLICY_NAME, "document": POLICY_DOCUMENT},
    )
    published = holder.data("POST", f"/v1/policies/{policy['id']}/publish")
    sdk = PaiziqSDK(
        policy=policy_from_document(POLICY_DOCUMENT),
        exporters=[
            HTTPExporter(
                base_url,
                api_key,
                batch_size=1,
                flush_interval_s=0.05,
                transport=transport,
            )
        ],
        notifiers=[
            ConsoleNotifier(),
            WebhookNotifier(f"{base_url}/v1/notifications", api_key=api_key),
        ],
        service_name=AGENT_NAME,
    )
    mandate = Mandate(
        principal_id=PRINCIPAL_ID,
        agent_id=agent["id"],
        max_amount=float(POLICY_DOCUMENT["hard_limit"]),
        currency="USD",
        allowed_merchants=[item["merchant"] for item in CATALOG],
        purpose="Company procurement from the approved catalog",
    )
    return Session(
        base_url=base_url,
        api_key=api_key,
        transport=transport,
        sdk=sdk,
        mandate=mandate,
        org=org,
        environment=env,
        agent=agent,
        policy={"id": policy["id"], "name": policy["name"], "version": published["version"]},
    )


def _wait_for_traces(session: Session) -> None:
    pending = {item.trace_id for item in session.purchases}
    deadline = time.time() + 5
    while pending and time.time() < deadline:
        ready = set()
        for trace_id in pending:
            response = session.transport.request("GET", f"/v1/traces/{trace_id}")
            body = response.json() if response.body else {}
            if isinstance(body, dict) and body.get("spans"):
                ready.add(trace_id)
        pending -= ready
        if pending:
            time.sleep(0.05)
    if pending:
        raise ProcurementError(f"traces not ingested: {sorted(pending)}")


def _issue_dashboard_key(session: Session) -> tuple[dict[str, Any], str]:
    record = session.data(
        "POST",
        "/v1/api-keys",
        {
            "env_id": session.environment["id"],
            "name": f"procurement-dashboard-{uuid.uuid4().hex[:6]}",
            "scope": "read",
            "role": "read_only",
        },
    )
    public = {
        "id": record["id"],
        "name": record["name"],
        "prefix": record.get("secret_prefix"),
        "role": record.get("role"),
        "scope": record.get("scope"),
    }
    return public, record["secret"]


def _preflight(base_url: str, origin: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url}/v1/agents",
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
            allowed = response.headers.get("access-control-allow-origin")
    except urllib.error.HTTPError as exc:
        status = exc.code
        allowed = (exc.headers or {}).get("access-control-allow-origin")
    return {
        "url": origin,
        "cors_ok": status == 200 and allowed == origin,
        "allowed_origin": allowed,
    }


@dataclass
class ProcurementResult:
    report: dict[str, Any]
    summary: str
    dashboard_key_secret: Optional[str]
    report_path: Optional[Path]


def run_procurement(
    base_url: str,
    api_key: str,
    model: Any,
    *,
    requisition: str = REQUISITION,
    dashboard_url: Optional[str] = None,
    issue_dashboard_key: bool = True,
    report_path: Optional[Path] = DEFAULT_REPORT,
) -> ProcurementResult:
    """Register the agent, let the model buy the requisition, publish results."""
    base_url = base_url.rstrip("/")
    _health(base_url)
    session = _open_session(base_url, api_key)
    # A name the handler will not treat as a payment tool. The purchase tool
    # records needs_review and rejected payments; the handler must not raise
    # before that happens. An empty set would fall back to the defaults.
    handler = create_langchain_handler(
        session.sdk,
        payment_tools={"__trace_only__"},
        agent_id=session.agent["id"],
        principal_id=PRINCIPAL_ID,
        enforce=False,
    )
    try:
        summary = run_agent(model, build_tools(session), handler, requisition)
        session.sdk.shutdown()
        _wait_for_traces(session)
    except ProcurementError:
        session.sdk.shutdown()
        raise
    secret = None
    key_public = None
    if issue_dashboard_key:
        key_public, secret = _issue_dashboard_key(session)
    report: dict[str, Any] = {
        "base_url": base_url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "org": {"id": session.org["id"], "name": session.org["name"]},
        "environment": {
            "id": session.environment["id"],
            "name": session.environment["name"],
            "kind": session.environment["kind"],
        },
        "agent": {
            "id": session.agent["id"],
            "name": session.agent["name"],
            "framework": "langchain",
        },
        "policy": session.policy,
        "requisition": requisition.strip(),
        "summary": summary,
        "purchases": [
            {
                "sku": item.sku,
                "merchant": item.merchant,
                "amount": item.amount,
                "quantity": item.quantity,
                "payment_id": item.payment_id,
                "verdict": item.verdict,
                "state": item.state,
                "reasons": item.reasons,
                "risk_flags": item.risk_flags,
                "trace_id": item.trace_id,
                "gateway_reference": item.gateway_reference,
            }
            for item in session.purchases
        ],
    }
    if key_public is not None:
        report["dashboard_key"] = key_public
    if dashboard_url:
        report["dashboard"] = _preflight(base_url, dashboard_url.rstrip("/"))
    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    return ProcurementResult(
        report=report,
        summary=summary,
        dashboard_key_secret=secret,
        report_path=report_path,
    )


def build_chat_model() -> Any:
    """OpenAI-compatible client. The key stays in the environment."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover
        raise ProcurementError("Install the chat client with: pip3 install langchain-openai") from exc
    if os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL"):
        return ChatOpenAI(
            model=os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile"),
            api_key=os.environ["LLM_API_KEY"],
            base_url=os.environ["LLM_BASE_URL"],
            temperature=0,
        )
    if os.environ.get("GROQ_API_KEY"):
        return ChatOpenAI(
            model=os.environ.get("LLM_MODEL", "openai/gpt-oss-120b"),
            api_key=os.environ["GROQ_API_KEY"],
            base_url="https://api.groq.com/openai/v1",
            temperature=0,
            max_tokens=1024,
        )
    if os.environ.get("OPENROUTER_API_KEY"):
        return ChatOpenAI(
            model=os.environ.get("LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
            temperature=0,
        )
    gemini = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gemini:
        return ChatOpenAI(
            model=os.environ.get("LLM_MODEL", "gemini-2.0-flash"),
            api_key=gemini,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            temperature=0,
        )
    raise ProcurementError(
        "No LLM API key in the environment. Set GROQ_API_KEY (free Llama tier at "
        "https://console.groq.com), OPENROUTER_API_KEY, or GEMINI_API_KEY."
    )


def _print_summary(result: ProcurementResult, base_url: str, dashboard_url: Optional[str]) -> None:
    report = result.report
    print("Procurement agent complete")
    print(f"  backend:      {base_url}")
    print(f"  organization: {report['org']['name']}  ({report['org']['id']})")
    print(f"  environment:  {report['environment']['name']}  ({report['environment']['id']})")
    print(f"  agent:        {report['agent']['name']}  ({report['agent']['id']})")
    print(f"  policy:       {report['policy']['name']} v{report['policy']['version']}")
    print()
    print(f"  {'sku':<14}{'amount':>8}  {'verdict':<13}{'state':<13}payment")
    for row in report["purchases"]:
        print(
            f"  {row['sku']:<14}{row['amount']:>8.2f}  {row['verdict']:<13}"
            f"{row['state']:<13}{row['payment_id']}"
        )
    if result.report_path is not None:
        print(f"\n  report: {result.report_path}")
    print()
    print("Next steps")
    login = f"{dashboard_url.rstrip('/')}/login" if dashboard_url else "<dashboard>/login"
    print(f"  1. Open {login}")
    print(f"  2. Backend URL: {base_url}")
    if result.dashboard_key_secret:
        print(f"  3. API key (read-only, shown once): {result.dashboard_key_secret}")
    print(
        f"  4. Select organization '{report['org']['name']}' and environment "
        f"'{report['environment']['name']}'"
    )
    print("  5. Payment Feed shows the purchases above; Reviews shows the held one")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the LangChain procurement agent")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("PAIZIQ_ENDPOINT", "http://127.0.0.1:8800"),
    )
    parser.add_argument(
        "--dashboard-url",
        default=os.environ.get("PAIZIQ_DASHBOARD_URL") or None,
    )
    parser.add_argument(
        "--out",
        default=os.environ.get("PAIZIQ_DEMO_REPORT", str(DEFAULT_REPORT)),
    )
    args = parser.parse_args(argv)
    api_key = os.environ.get("PAIZIQ_API_KEY", "")
    if not api_key:
        print("PAIZIQ_API_KEY is required", file=sys.stderr)
        return 2
    try:
        result = run_procurement(
            args.endpoint,
            api_key,
            build_chat_model(),
            dashboard_url=args.dashboard_url,
            report_path=Path(args.out),
        )
    except ProcurementError as exc:
        print(f"Procurement agent failed: {exc}", file=sys.stderr)
        return 1
    _print_summary(result, args.endpoint.rstrip("/"), args.dashboard_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
