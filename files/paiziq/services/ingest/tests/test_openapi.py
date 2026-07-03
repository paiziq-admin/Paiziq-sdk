"""OpenAPI contract tests: the committed spec and the generated SDK
client types must stay in sync with the live FastAPI app."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_INGEST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_INGEST_DIR))
sys.path.insert(0, str(_INGEST_DIR / "scripts"))

from app import app  # noqa: E402
from gen_api_types import render_types  # noqa: E402

SPEC_PATH = _INGEST_DIR / "openapi.json"
TYPES_PATH = _INGEST_DIR.parents[1] / "sdk" / "src" / "paiziq" / "api_types.py"


def test_committed_spec_matches_app():
    committed = json.loads(SPEC_PATH.read_text())
    live = json.loads(json.dumps(app.openapi()))  # normalize tuples/keys
    assert committed == live, "openapi.json is stale — run `make openapi`"


def test_generated_client_types_match_spec():
    expected = render_types(app.openapi())
    assert TYPES_PATH.read_text() == expected, (
        "sdk/src/paiziq/api_types.py is stale — run `make openapi`"
    )


def test_spec_covers_frozen_wire_models():
    schemas = app.openapi()["components"]["schemas"]
    assert {"SpanIn", "TraceBatch", "NotificationIn"} <= set(schemas)
    # Frozen v1 contract: required span identity fields never change.
    assert set(schemas["SpanIn"]["required"]) == {"name", "trace_id", "span_id"}


def test_spec_lists_frozen_ingest_paths():
    paths = app.openapi()["paths"]
    for route in ("/health", "/v1/traces", "/v1/notifications", "/v1/traces/{trace_id}"):
        assert route in paths
