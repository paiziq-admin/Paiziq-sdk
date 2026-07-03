"""Export the OpenAPI spec and regenerate the SDK client types.

Run via `make openapi`. Writes:
    services/ingest/openapi.json     — committed machine-readable contract
    sdk/src/paiziq/api_types.py      — stdlib-only TypedDicts for the wire

tests/test_openapi.py fails if either artifact drifts from the app.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_INGEST_DIR = _SCRIPTS_DIR.parent
_REPO_ROOT = _INGEST_DIR.parents[1]

sys.path.insert(0, str(_INGEST_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR))

from app import app  # noqa: E402
from gen_api_types import render_types  # noqa: E402

SPEC_PATH = _INGEST_DIR / "openapi.json"
TYPES_PATH = _REPO_ROOT / "sdk" / "src" / "paiziq" / "api_types.py"


def main() -> None:
    spec = app.openapi()
    SPEC_PATH.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    TYPES_PATH.write_text(render_types(spec))
    print(f"wrote {SPEC_PATH}")
    print(f"wrote {TYPES_PATH}")


if __name__ == "__main__":
    main()
