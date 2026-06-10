"""Framework integrations.

The SDK never imports a framework at module load. Each integration is
constructed lazily so `pip install paiziq` works with zero extras, and
LangChain/OpenAI hooks activate only when those packages are present.

Three integration styles, all funneling into the same Tracer/SDK:

1. Generic   — `@instrument_payment_tool` decorator for ANY framework
               (CrewAI, AutoGen, custom orchestration, plain functions).
2. LangChain — `PaiziqCallbackHandler`, a BaseCallbackHandler that
               captures tool/LLM events and intercepts payment tools.
3. OpenAI    — `guard_tool_call`, applied where the app dispatches
               function/tool calls coming back from the model.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from ...models import DecisionStatus, PaymentRequest

if TYPE_CHECKING:  # pragma: no cover
    from ...sdk import PaiziqSDK

logger = logging.getLogger("paiziq.integrations")


class PaymentBlockedError(RuntimeError):
    """Raised back into the agent loop when Paiziq blocks a payment tool call."""

    def __init__(self, decision) -> None:
        self.decision = decision
        super().__init__(
            f"Paiziq blocked payment ({decision.status.value}): {'; '.join(decision.reasons)}"
        )


# ── 1. Generic decorator (framework-agnostic) ───────────────────────────────

def instrument_payment_tool(
    sdk: "PaiziqSDK",
    extract: Callable[..., PaymentRequest],
    enforce: bool = True,
):
    """Wrap any callable that executes a payment.

    `extract` maps the wrapped function's (*args, **kwargs) to a
    PaymentRequest. Before the call, Paiziq reviews the payment and
    traces the verdict. If `enforce` and the verdict is not approved,
    PaymentBlockedError is raised instead of executing the tool.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            request = extract(*args, **kwargs)
            decision = sdk.review_payment(request)
            if enforce and decision.status is not DecisionStatus.APPROVED:
                raise PaymentBlockedError(decision)
            with sdk.tracer.span(
                "payment_tool.execute",
                {"paiziq.request_id": request.request_id, "tool.name": fn.__name__},
            ):
                return fn(*args, **kwargs)

        return wrapper

    return decorator


# ── 2. LangChain callback handler ───────────────────────────────────────────

def _payment_request_from_tool_input(tool_input: Any, defaults: dict[str, Any]) -> Optional[PaymentRequest]:
    """Best-effort mapping of a LangChain tool input to a PaymentRequest."""
    data: dict[str, Any]
    if isinstance(tool_input, str):
        try:
            data = json.loads(tool_input)
        except (ValueError, TypeError):
            return None
    elif isinstance(tool_input, dict):
        data = tool_input
    else:
        return None
    if "merchant" not in data or "amount" not in data:
        return None
    try:
        return PaymentRequest(
            agent_id=defaults.get("agent_id", "langchain-agent"),
            principal_id=defaults.get("principal_id", "unknown"),
            merchant=str(data["merchant"]),
            amount=float(data["amount"]),
            currency=str(data.get("currency", defaults.get("currency", "USD"))),
            category=str(data.get("category", "general")),
            intent_description=str(data.get("intent", data.get("reason", ""))),
            metadata={"source": "langchain"},
        )
    except (TypeError, ValueError):
        return None


def create_langchain_handler(
    sdk: "PaiziqSDK",
    payment_tools: Optional[set[str]] = None,
    agent_id: str = "langchain-agent",
    principal_id: str = "unknown",
    enforce: bool = True,
):
    """Build a LangChain BaseCallbackHandler bound to a PaiziqSDK.

    Requires `pip install paiziq[langchain]`. Traces every LLM/tool
    event; for tools whose name is in `payment_tools`, runs the payment
    review and (optionally) raises PaymentBlockedError to halt the call.
    """
    try:
        from langchain_core.callbacks import BaseCallbackHandler
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "LangChain integration requires langchain-core. "
            "Install with: pip install paiziq[langchain]"
        ) from exc

    defaults = {"agent_id": agent_id, "principal_id": principal_id}
    watched = {t.lower() for t in (payment_tools or {"execute_payment", "make_payment", "pay"})}

    class PaiziqCallbackHandler(BaseCallbackHandler):
        raise_error = True

        def on_llm_start(self, serialized, prompts, **kwargs):
            with sdk.tracer.span("llm.start", {"prompt_count": len(prompts)}) as s:
                s.add_event("prompts", {"preview": [p[:300] for p in prompts]})

        def on_llm_end(self, response, **kwargs):
            with sdk.tracer.span("llm.end"):
                pass

        def on_tool_start(self, serialized, input_str, **kwargs):
            name = (serialized or {}).get("name", "tool")
            with sdk.tracer.span("tool.start", {"tool.name": name}) as s:
                s.add_event("input", {"preview": str(input_str)[:500]})
            if name.lower() in watched:
                request = _payment_request_from_tool_input(input_str, defaults)
                if request is None:
                    logger.warning("Paiziq could not parse payment input for tool '%s'", name)
                    return
                decision = sdk.review_payment(request)
                if enforce and decision.status is not DecisionStatus.APPROVED:
                    raise PaymentBlockedError(decision)

        def on_tool_end(self, output, **kwargs):
            with sdk.tracer.span("tool.end"):
                pass

        def on_chain_error(self, error, **kwargs):
            with sdk.tracer.span("chain.error", {"error": str(error)[:300]}):
                pass

    return PaiziqCallbackHandler()


# ── 3. OpenAI tool-call guard ───────────────────────────────────────────────

def guard_tool_call(
    sdk: "PaiziqSDK",
    tool_name: str,
    arguments: str | dict[str, Any],
    payment_tools: Optional[set[str]] = None,
    agent_id: str = "openai-agent",
    principal_id: str = "unknown",
):
    """Review an OpenAI function/tool call before the app dispatches it.

    Usage inside the standard tool-dispatch loop:

        for call in response.choices[0].message.tool_calls:
            paiziq.guard_tool_call(sdk, call.function.name, call.function.arguments)
            result = dispatch(call)   # only reached if approved

    Returns the Decision for non-payment tools (approved, pass-through)
    and raises PaymentBlockedError for blocked payment tools.
    """
    watched = {t.lower() for t in (payment_tools or {"execute_payment", "make_payment", "pay"})}
    with sdk.tracer.span("openai.tool_call", {"tool.name": tool_name}):
        if tool_name.lower() not in watched:
            return None
        request = _payment_request_from_tool_input(
            arguments, {"agent_id": agent_id, "principal_id": principal_id}
        )
        if request is None:
            logger.warning("Paiziq could not parse payment arguments for '%s'", tool_name)
            return None
        decision = sdk.review_payment(request)
        if decision.status is not DecisionStatus.APPROVED:
            raise PaymentBlockedError(decision)
        return decision
