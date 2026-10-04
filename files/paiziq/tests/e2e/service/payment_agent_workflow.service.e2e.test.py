"""Local service-integration E2E for the payment-agent workflow.

AC: A payment above the published fake review threshold is
needs_review with over_review_threshold, execution stops on the
4-way policy check, the tracer exports that threshold on the review
span, and the dashboard shows the agent, the threshold, the decision,
and the correlated trace.
Behavior: one secure payment agent, one published fake policy, one SDK
review/execute transaction, asserted through ingest and the live
dashboard UI.
@category service-integration-e2e
@lane service-integration-e2e
@dependency full-system
@complexity high
ROI: 110 (BV 10 * Freq 10 + legal 0 + DefectDetection 10).
Evidence is docs/e2e/PAYMENT_AGENT_WORKFLOW_E2E_PLAN.md.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from paiziq import Mandate, PaiziqSDK, PaymentPolicy, PaymentRequest
from paiziq.tracing.tracer import HTTPExporter, InMemoryExporter

# Documented development bootstrap key. Not a deployed secret.
DEV_KEY = "dev-key"
AGENT_NAME = "secure-payment-agent"
POLICY_NAME = "e2e-fake-threshold-policy"
REVIEW_THRESHOLD = 42.5
HARD_LIMIT = 500.0
AMOUNT = 75.0
MERCHANT = "acme corp"
PRINCIPAL = "user-42"

_BROWSER_JS = r"""
const { chromium } = require("@playwright/test");
const fs = require("fs");

async function main() {
  const env = process.env;
  const findings = {
    agentVisible: false,
    policyNameVisible: false,
    reviewThreshold: null,
    decisionVisible: false,
    reasonHasThreshold: false,
    amountVisible: false,
    traceHeading: null,
    traceShowsReviewSpan: false,
    clicked: [],
    error: null,
  };
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on("dialog", (dialog) => dialog.accept());
  try {
    await page.goto(env.DASH_URL + "/login", { waitUntil: "domcontentloaded" });
    await page.locator("#login-endpoint").fill(env.API_URL);
    await page.locator("#login-api-key").fill(env.API_KEY);
    await page.getByRole("button", { name: "Connect dashboard" }).click();
    await page.getByRole("link", { name: "Agents & SDK" }).waitFor({ timeout: 20000 });
    findings.clicked.push("login");
    await page.getByRole("link", { name: "Agents & SDK" }).click();
    await page.getByText(env.AGENT_NAME, { exact: true }).waitFor({ timeout: 20000 });
    findings.agentVisible = true;
    findings.clicked.push("agents");
    await page.getByRole("link", { name: "Risk Policies" }).click();
    await page.getByText(env.POLICY_NAME, { exact: true }).waitFor({ timeout: 20000 });
    findings.policyNameVisible = true;
    await page.getByLabel("Review threshold").waitFor({ timeout: 20000 });
    findings.reviewThreshold = await page.getByLabel("Review threshold").inputValue();
    findings.clicked.push("policies");
    await page.goto(env.DASH_URL + "/payments/" + env.PAYMENT_ID, {
      waitUntil: "domcontentloaded",
    });
    await page.getByText("Needs Review").first().waitFor({ timeout: 20000 });
    findings.decisionVisible = true;
    await page.getByText(/No correlated trace found|paiziq\.review_payment/).waitFor({
      timeout: 20000,
    });
    const body = await page.locator("body").innerText();
    findings.reasonHasThreshold = body.includes("42.50");
    findings.amountVisible = body.includes(env.AMOUNT_TEXT);
    const traceMissing = body.includes("No correlated trace found");
    const spanVisible = body.includes("paiziq.review_payment");
    findings.traceHeading = traceMissing ? "No correlated trace found" : "span";
    findings.traceShowsReviewSpan = spanVisible && !traceMissing;
    findings.clicked.push("payment-detail");
    if (env.SCREENSHOT) await page.screenshot({ path: env.SCREENSHOT, fullPage: true });
  } catch (err) {
    findings.error = String(err && err.stack ? err.stack : err);
    try { findings.pageText = await page.locator("body").innerText(); } catch (_e) {}
    if (env.SCREENSHOT) {
      try { await page.screenshot({ path: env.SCREENSHOT, fullPage: true }); } catch (_e) {}
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(env.FINDINGS, JSON.stringify(findings));
}

main().catch((err) => {
  fs.writeFileSync(process.env.FINDINGS, JSON.stringify({
    error: String(err && err.stack ? err.stack : err),
    clicked: [],
    traceShowsReviewSpan: false,
  }));
  process.exit(1);
});
"""



def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _paiziq_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _dashboard_dir() -> Path:
    override = os.environ.get("PAIZIQ_DASHBOARD_DIR")
    if override:
        return Path(override)
    return _paiziq_root().parents[1].parent / "Paiziq-Dashboard"



class LocalStack:
    """Ingest plus Vite, both on ephemeral localhost ports."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="paiziq-e2e-"))
        self.procs: list[subprocess.Popen[bytes]] = []
        self.logs: list[object] = []
        self.ingest_port = _free_port()
        self.dash_port = _free_port()
        self.ingest_log = self.root / "ingest.log"
        self.dash_log = self.root / "dashboard.log"
        self.screenshot = self.root / "payment-detail.png"

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.ingest_port}"

    @property
    def dash_url(self) -> str:
        return f"http://127.0.0.1:{self.dash_port}"


    def start(self) -> None:
        python = _paiziq_root() / "sdk" / ".venv" / "bin" / "python"
        ingest = _paiziq_root() / "services" / "ingest"
        dashboard = _dashboard_dir()
        if not python.is_file():
            raise RuntimeError(f"missing {python}; run make install from files/paiziq")
        if not (dashboard / "node_modules").is_dir():
            raise RuntimeError(f"missing {dashboard}/node_modules; run npm ci")
        ingest_env = os.environ.copy()
        ingest_env["PAIZIQ_ENV"] = "development"
        ingest_env["PAIZIQ_INGEST_DB"] = str(self.root / "ingest.db")
        ingest_env["PAIZIQ_INGEST_KEYS"] = DEV_KEY
        ingest_env["PAIZIQ_CORS_ORIGINS"] = self.dash_url
        ingest_env["PAIZIQ_LOG_LEVEL"] = "WARNING"
        self._spawn(
            [str(python), "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(self.ingest_port), "--log-level", "warning"],
            cwd=ingest, env=ingest_env, log_path=self.ingest_log,
        )
        self._spawn(
            ["npm", "run", "dev", "--", "--host", "127.0.0.1",
             "--port", str(self.dash_port), "--strictPort"],
            cwd=dashboard, env=os.environ.copy(), log_path=self.dash_log,
        )
        _wait_http(f"{self.api_url}/health", self.procs[0], self.ingest_log, 30)
        _wait_http(f"{self.dash_url}/", self.procs[1], self.dash_log, 90)


    def _spawn(self, argv: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> None:
        handle = log_path.open("w", encoding="utf-8")
        self.logs.append(handle)
        self.procs.append(subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT,
            start_new_session=True,
        ))

    def stop(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.time() + 5
        for proc in self.procs:
            if proc.poll() is not None:
                continue
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for handle in self.logs:
            handle.close()


def _wait_http(url: str, proc: subprocess.Popen[bytes], log_path: Path, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    last_error = "no response"
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(encoding="utf-8")[-2000:]
            raise RuntimeError(f"process exited {proc.returncode} before {url}\n{tail}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.1)
    tail = log_path.read_text(encoding="utf-8")[-2000:]
    raise RuntimeError(f"timed out waiting for {url}: {last_error}\n{tail}")



def _request(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {DEV_KEY}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        parsed = json.loads(raw) if raw else {}
        return exc.code, parsed


def _ok(method: str, url: str, body: dict | None = None) -> dict:
    status, payload = _request(method, url, body)
    assert status == 200, (status, payload)
    assert payload.get("success") is True, payload
    return payload


def _dashboard_trace(api_url: str, payment: dict) -> dict | None:
    """Same lookup order as PaymentDetailScreen.fetchRelatedTrace."""
    candidates = []
    for key in ("request_id", "id"):
        value = payment.get(key)
        if value and value not in candidates:
            candidates.append(value)
    for candidate in candidates:
        status, payload = _request("GET", f"{api_url}/v1/traces/{candidate}")
        if status == 200 and payload.get("spans"):
            return payload
    for candidate in candidates:
        quoted = "\"" + candidate.replace("\"", "\"\"") + "\""
        query = urllib.parse.urlencode({"q": quoted, "limit": 10, "offset": 0})
        status, payload = _request("GET", f"{api_url}/v1/search/events?{query}")
        if status != 200 or not payload.get("success"):
            continue
        seen: list[str] = []
        for item in payload.get("data") or []:
            trace_id = item.get("trace_id")
            if not trace_id or trace_id in seen:
                continue
            seen.append(trace_id)
            trace_status, trace = _request("GET", f"{api_url}/v1/traces/{trace_id}")
            if trace_status == 200 and trace.get("spans"):
                return trace
    return None



def _drive_browser(stack: LocalStack, payment_id: str) -> dict:
    script = stack.root / "browse.js"
    findings_path = stack.root / "findings.json"
    script.write_text(_BROWSER_JS, encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "DASH_URL": stack.dash_url,
        "API_URL": stack.api_url,
        "API_KEY": DEV_KEY,
        "AGENT_NAME": AGENT_NAME,
        "POLICY_NAME": POLICY_NAME,
        "PAYMENT_ID": payment_id,
        "AMOUNT_TEXT": "$75.00 USD",
        "SCREENSHOT": str(stack.screenshot),
        "FINDINGS": str(findings_path),
    })
    completed = subprocess.run(
        ["node", str(script)],
        cwd=_dashboard_dir(),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if not findings_path.is_file():
        pytest.fail(
            "browser driver wrote no findings\n"
            f"stdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-2000:]}"
        )
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    findings["returncode"] = completed.returncode
    if completed.stderr:
        findings["stderr_tail"] = completed.stderr[-1500:]
    return findings



def test_published_threshold_holds_traced_payment_on_dashboard() -> None:
    # Arrange: local ingest, dashboard, agent, and fake published policy.
    stack = LocalStack()
    try:
        stack.start()
        api = stack.api_url
        org = _ok("POST", f"{api}/v1/orgs", {"name": "e2e-payment-org"})["data"]
        env = _ok(
            "POST",
            f"{api}/v1/orgs/" + org["id"] + "/environments",
            {"name": "sandbox", "kind": "sandbox"},
        )["data"]
        agent = _ok(
            "POST",
            f"{api}/v1/agents",
            {"env_id": env["id"], "name": AGENT_NAME, "framework": "paiziq-sdk"},
        )["data"]
        policy = _ok(
            "POST",
            f"{api}/v1/policies",
            {
                "env_id": env["id"],
                "name": POLICY_NAME,
                "document": {
                    "review_threshold": REVIEW_THRESHOLD,
                    "hard_limit": HARD_LIMIT,
                    "known_merchants": [MERCHANT],
                },
            },
        )["data"]
        published = _ok("POST", f"{api}/v1/policies/" + policy["id"] + "/publish")["data"]
        assert published["version"] == 1
        assert published["document"]["review_threshold"] == REVIEW_THRESHOLD
        memory = InMemoryExporter()
        exporter = HTTPExporter(api, DEV_KEY, batch_size=1, flush_interval_s=0.05)
        sdk = PaiziqSDK(
            policy=PaymentPolicy(
                review_threshold=REVIEW_THRESHOLD,
                hard_limit=HARD_LIMIT,
                known_merchants={MERCHANT},
            ),
            exporters=[memory, exporter],
            service_name=AGENT_NAME,
        )
        request = PaymentRequest(
            agent_id=agent["id"],
            principal_id=PRINCIPAL,
            merchant=MERCHANT,
            amount=AMOUNT,
            currency="USD",
            intent_description="Renew Acme subscription",
            mandate=Mandate(
                principal_id=PRINCIPAL,
                agent_id=agent["id"],
                max_amount=HARD_LIMIT,
                allowed_merchants=[MERCHANT],
                purpose="SaaS spend",
            ),
        )
        # Act: SDK transaction, then the control-plane decision for that payment.
        decision = sdk.review_payment(request)
        execution = sdk.execute_payment(request)
        sdk.shutdown()
        review_span = next(span for span in memory.spans if span.name == "paiziq.review_payment")
        deadline = time.time() + 5
        trace: dict = {}
        while time.time() < deadline:
            status, trace = _request("GET", f"{api}/v1/traces/{review_span.trace_id}")
            if status == 200 and trace.get("spans"):
                break
            time.sleep(0.05)
        payment = _ok(
            "POST",
            f"{api}/v1/payments",
            {
                "env_id": env["id"],
                "agent_id": agent["id"],
                "principal_id": PRINCIPAL,
                "merchant": MERCHANT,
                "amount": AMOUNT,
                "currency": "USD",
                "intent_description": "Renew Acme subscription",
                "request_id": request.request_id,
            },
        )["data"]
        server_decision = _ok(
            "POST", f"{api}/v1/decisions", {"payment_id": payment["id"]},
        )["data"]


        # Assert: decision, tracer threshold, and the dashboard screens.
        assert decision.status.value == "needs_review"
        assert "over_review_threshold" in [flag.value for flag in decision.risk_flags]
        threshold_rule = next(
            rule for rule in decision.rule_results if rule.rule_name == "threshold_check"
        )
        assert threshold_rule.details["review_threshold"] == REVIEW_THRESHOLD
        assert execution.executed is False
        assert execution.error is not None and "policy_match" in execution.error
        assert server_decision["verdict"] == "needs_review"
        assert server_decision["policy_version"] == 1
        assert "over_review_threshold" in server_decision["risk_flags"]
        assert any("42.50" in reason for reason in server_decision["reasons"])
        assert str(server_decision["review_id"]).startswith("rev_")
        agents = _ok("GET", f"{api}/v1/agents?env_id={env["id"]}&limit=200&offset=0")["data"]
        assert any(item["name"] == AGENT_NAME and item["status"] == "active" for item in agents)
        visible_policy = _ok("GET", f"{api}/v1/policies/" + policy["id"])["data"]
        assert visible_policy["draft_document"]["review_threshold"] == REVIEW_THRESHOLD
        detail = _ok("GET", f"{api}/v1/payments/" + payment["id"])["data"]
        assert detail["state"] == "needs_review"
        listed = _ok(
            "GET", f"{api}/v1/decisions?payment_id=" + payment["id"] + "&limit=200&offset=0",
        )["data"]
        assert listed[0]["verdict"] == "needs_review"
        exported = next(span for span in trace["spans"] if span["name"] == "paiziq.review_payment")
        event = next(item for item in exported["events"] if item["name"] == "decision")
        exported_rule = next(
            rule for rule in event["payload"]["rule_results"] if rule["rule"] == "threshold_check"
        )
        assert exported_rule["details"]["review_threshold"] == REVIEW_THRESHOLD
        assert event["payload"]["status"] == "needs_review"
        findings = _drive_browser(stack, payment["id"])
        related = _dashboard_trace(api, payment)
        assert findings.get("error") is None, findings
        assert findings["agentVisible"] is True
        assert findings["policyNameVisible"] is True
        assert findings["reviewThreshold"] in {"42.5", "42.50"}
        assert findings["decisionVisible"] is True
        assert findings["reasonHasThreshold"] is True
        assert findings["amountVisible"] is True
        assert findings["traceShowsReviewSpan"] is True, {
            "ui": findings,
            "sdk_trace_id": review_span.trace_id,
            "payment_request_id": request.request_id,
            "dashboard_lookup_found": related is not None,
        }
    finally:
        stack.stop()
