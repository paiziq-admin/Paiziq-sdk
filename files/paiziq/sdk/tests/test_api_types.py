"""Generated API client types: importable from the top-level package,
stdlib-only, and aligned with the SDK's real wire output."""

from __future__ import annotations

import paiziq
from paiziq import api_types
from paiziq.tracing.tracer import Span


def test_api_types_reexported_from_package():
    assert "api_types" in paiziq.__all__
    assert paiziq.api_types is api_types


def test_generated_names_exported():
    for name in api_types.__all__:
        assert hasattr(api_types, name)
    assert {"SpanIn", "TraceBatch", "NotificationIn"} <= set(api_types.__all__)


def test_span_wire_dict_fits_span_in_type():
    span = Span(name="paiziq.review_payment", trace_id="tr1")
    span.add_event("decision", {"verdict": "approved"})
    span.end("ok")
    wire = span.to_dict()
    allowed = set(api_types.SpanIn.__annotations__)
    assert set(wire) <= allowed, f"wire keys {set(wire) - allowed} missing from SpanIn"
    required = {"name", "trace_id", "span_id"}
    assert required <= set(wire)
