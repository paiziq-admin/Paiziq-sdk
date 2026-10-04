"""LangChain procurement agent against the ingest control plane.

# AC: A LangChain tool-calling agent buys the company catalog through
# PaiziqSDK. The published procurement policy approves paper, holds the
# hosting renewal for review, and rejects the blocklisted toner. Traces
# reach ingest and a read-only dashboard key cannot mint keys.
# Behavior: scripted chat model -> purchase_product tool -> SDK review,
# mock gateway, control-plane decision.
# @category: integration
# @lane: integration
# @dependency: langchain-core, paiziq SDK, ingest API, SQLite
# @complexity: high
# ROI: 95

The chat model is scripted so the lane does not call a hosted LLM.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

_INGEST = Path(__file__).resolve().parents[1]
_EXAMPLES = _INGEST.parents[1] / "sdk" / "examples"
for entry in (_INGEST, _EXAMPLES):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from app import app  # noqa: E402
from paiziq.transport import RetryPolicy, SyncHTTPTransport  # noqa: E402

import procurement_agent as agent  # noqa: E402


class ScriptedChatModel(BaseChatModel):
    """Returns a fixed sequence of tool calls, then a final answer."""

    replies: list[AIMessage]
    index: list[int] = Field(default_factory=lambda: [0])

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        message = self.replies[self.index[0]]
        self.index[0] += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def _call(sku: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "purchase_product", "args": {"sku": sku, "quantity": 1}, "id": call_id}],
    )


@pytest.fixture(scope="module")
def base_url():
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="procurement-uvicorn", daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            pytest.fail("uvicorn did not start within 10s")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def result(base_url, tmp_path_factory):
    model = ScriptedChatModel(
        replies=[
            _call("PAPER-A4", "c1"),
            _call("HOST-ANNUAL", "c2"),
            _call("TONER-GREY", "c3"),
            AIMessage(content="Purchases submitted."),
        ]
    )
    return agent.run_procurement(
        base_url,
        "dev-key",
        model,
        report_path=tmp_path_factory.mktemp("procurement") / "run.json",
    )


def test_agent_buys_the_requisition_under_the_published_policy(result):
    rows = {item["sku"]: item for item in result.report["purchases"]}
    assert set(rows) == {"PAPER-A4", "HOST-ANNUAL", "TONER-GREY"}
    assert rows["PAPER-A4"]["verdict"] == "approved"
    assert rows["PAPER-A4"]["state"] == "executed"
    assert rows["PAPER-A4"]["gateway_reference"]
    assert rows["HOST-ANNUAL"]["verdict"] == "needs_review"
    assert rows["HOST-ANNUAL"]["state"] == "needs_review"
    assert "over_review_threshold" in rows["HOST-ANNUAL"]["risk_flags"]
    assert rows["TONER-GREY"]["verdict"] == "rejected"
    assert rows["TONER-GREY"]["state"] == "rejected"
    assert "merchant_blocked" in rows["TONER-GREY"]["risk_flags"]
    assert result.report["policy"]["version"] == 1
    assert result.report["agent"]["framework"] == "langchain"
    assert result.summary == "Purchases submitted."


def test_dashboard_key_is_read_only_and_absent_from_the_report(result, base_url):
    assert result.dashboard_key_secret
    text = result.report_path.read_text()
    assert result.dashboard_key_secret not in text
    assert "dev-key" not in text
    transport = SyncHTTPTransport(
        base_url, api_key=result.dashboard_key_secret, retry=RetryPolicy(max_attempts=1)
    )
    env_id = result.report["environment"]["id"]
    listing = transport.request("GET", f"/v1/agents?env_id={env_id}&limit=5")
    assert listing.status == 200
    forbidden = transport.request(
        "POST",
        "/v1/api-keys",
        json_body={"env_id": env_id, "name": "escalation", "scope": "admin"},
    )
    assert forbidden.status == 403


def test_review_and_rejection_are_placed_on_the_alert_feed(result, base_url):
    transport = SyncHTTPTransport(base_url, api_key="dev-key", retry=RetryPolicy(max_attempts=1))
    notes = transport.request("GET", "/v1/notifications").json()["notifications"]
    agent_id = result.report["agent"]["id"]
    mine = [note for note in notes if agent_id in note["message"]]
    by_title = {note["title"]: note for note in mine}
    assert set(by_title) == {"Payment awaiting your review", "Payment rejected"}
    assert by_title["Payment awaiting your review"]["severity"] == "info"
    assert "480.00" in by_title["Payment awaiting your review"]["message"]
    assert by_title["Payment rejected"]["severity"] == "warning"
    assert "grey market surplus" in by_title["Payment rejected"]["message"]
