"""Render stdlib-only TypedDict client types from an OpenAPI spec.

Used by export_openapi.py (regeneration) and tests/test_openapi.py
(sync check). Only component schemas are rendered; the output module
imports nothing beyond `typing`, so it satisfies the SDK's
zero-dependency-core invariant.
"""

from __future__ import annotations

from typing import Any

_HEADER = '''"""Generated API client types for the Paiziq ingest wire contract.

GENERATED FILE — do not edit by hand. Regenerate with `make openapi`
(runs services/ingest/scripts/export_openapi.py).

Source: {title} v{version} (OpenAPI {openapi}).
"""

from __future__ import annotations

from typing import Any, TypedDict

__all__ = {all_names}
'''


def _py_type(schema: dict[str, Any] | None) -> str:
    """Map a JSON-schema fragment to a Python annotation string."""
    if not schema:
        return "Any"
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "anyOf" in schema:
        parts: list[str] = []
        for sub in schema["anyOf"]:
            mapped = _py_type(sub)
            if mapped not in parts:
                parts.append(mapped)
        return " | ".join(parts)
    kind = schema.get("type")
    if kind == "string":
        return "str"
    if kind == "integer":
        return "int"
    if kind == "number":
        return "float"
    if kind == "boolean":
        return "bool"
    if kind == "null":
        return "None"
    if kind == "array":
        return f"list[{_py_type(schema.get('items'))}]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"dict[str, {_py_type(extra)}]"
        return "dict[str, Any]"
    return "Any"


def _field_lines(props: dict[str, Any], names: list[str]) -> str:
    return "\n".join(f"    {name}: {_py_type(props[name])}" for name in names)


def _class_src(name: str, schema: dict[str, Any]) -> str:
    """Render one component schema as TypedDict source (3.10-compatible)."""
    props: dict[str, Any] = schema.get("properties", {})
    required = [p for p in props if p in set(schema.get("required", []))]
    optional = [p for p in props if p not in set(schema.get("required", []))]
    if not props:
        return f"{name} = dict[str, Any]\n"
    if required and optional:
        return (
            f"class _{name}Required(TypedDict):\n{_field_lines(props, required)}\n\n\n"
            f"class {name}(_{name}Required, total=False):\n{_field_lines(props, optional)}\n"
        )
    if required:
        return f"class {name}(TypedDict):\n{_field_lines(props, required)}\n"
    return f"class {name}(TypedDict, total=False):\n{_field_lines(props, optional)}\n"


def render_types(spec: dict[str, Any]) -> str:
    """Render the full api_types.py module source for an OpenAPI spec."""
    schemas: dict[str, Any] = spec.get("components", {}).get("schemas", {})
    names = sorted(schemas)
    info = spec.get("info", {})
    header = _HEADER.format(
        title=info.get("title", "unknown"),
        version=info.get("version", "unknown"),
        openapi=spec.get("openapi", "unknown"),
        all_names=repr(names),
    )
    bodies = [_class_src(name, schemas[name]) for name in names]
    return header + "\n\n" + "\n\n".join(bodies)
