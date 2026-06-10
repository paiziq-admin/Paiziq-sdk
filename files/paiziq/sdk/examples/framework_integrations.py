"""Framework integration examples.

These snippets require the optional extras:
    pip install paiziq[langchain]   or   pip install paiziq[openai]
"""

from paiziq import PaiziqSDK, PaymentPolicy, PaymentBlockedError, guard_tool_call

sdk = PaiziqSDK(policy=PaymentPolicy(review_threshold=100, hard_limit=1000))


# ── LangChain ────────────────────────────────────────────────────────────────
# Attach the Paiziq callback handler; every LLM/tool event is traced, and
# tools named in `payment_tools` are reviewed before they run.
def langchain_example():
    from paiziq import create_langchain_handler
    # from langchain.agents import AgentExecutor ...

    handler = create_langchain_handler(
        sdk,
        payment_tools={"execute_payment"},
        agent_id="procurement-agent",
        principal_id="user-42",
    )
    # agent_executor.invoke({"input": "renew our Acme subscription"},
    #                       config={"callbacks": [handler]})
    return handler


# ── OpenAI SDK ───────────────────────────────────────────────────────────────
# Guard each function/tool call returned by the model before dispatching it.
def openai_example(response):
    for call in response.choices[0].message.tool_calls or []:
        try:
            guard_tool_call(
                sdk,
                call.function.name,
                call.function.arguments,
                agent_id="procurement-agent",
                principal_id="user-42",
            )
        except PaymentBlockedError as blocked:
            # Feed the verdict back to the model instead of executing.
            print("Blocked:", blocked.decision.reasons)
            continue
        # ... dispatch the tool normally — Paiziq approved it ...
