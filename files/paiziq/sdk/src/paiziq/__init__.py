"""Paiziq Agent Audit Tracer SDK.

Public surface:
    PaiziqSDK, PaymentRequest, Mandate, PaymentPolicy, Decision,
    DecisionStatus, RiskFlag, ExecutionResult
Integrations:
    paiziq.tracing.integrations (generic / LangChain / OpenAI)
"""

from . import api_types
from .audit.postgres import PostgresAuditStore
from .engine.policy import BudgetTracker, PaymentPolicy
from .engine.stores import RedisBudgetStore
from .models import (
    Decision,
    DecisionStatus,
    ExecutionResult,
    Mandate,
    PaymentRequest,
    RiskFlag,
)
from .sdk import PaiziqSDK
from .tracing.scrub import PIIScrubber, ScrubbingExporter
from .tracing.integrations import (
    PaymentBlockedError,
    create_langchain_handler,
    guard_tool_call,
    instrument_payment_tool,
)

__version__ = "0.2.0"

__all__ = [
    "PaiziqSDK",
    "api_types",
    "PaymentRequest",
    "Mandate",
    "PaymentPolicy",
    "BudgetTracker",
    "RedisBudgetStore",
    "PostgresAuditStore",
    "PIIScrubber",
    "ScrubbingExporter",
    "Decision",
    "DecisionStatus",
    "RiskFlag",
    "ExecutionResult",
    "PaymentBlockedError",
    "instrument_payment_tool",
    "create_langchain_handler",
    "guard_tool_call",
]
