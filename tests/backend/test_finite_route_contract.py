"""OpenAPI guard for the finite browser-route stream contract."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.backend.main import app

# Chat send does not use the canonical UI stream for its request-scoped content:
# docs/BACKEND.md documents POST /api/ui/responses as delivering it through
# GET /api/ui/responses/{request_id}/stream instead.
CLIENT_ID_EXCEPTIONS = {
    ("post", "/api/ui/responses"),
}


def _resolve(schema: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in value:
        ref = value["$ref"]
        assert ref.startswith("#/"), f"unsupported OpenAPI reference: {ref}"
        resolved: Any = schema
        for part in ref.removeprefix("#/").split("/"):
            resolved = resolved[part]
        assert isinstance(resolved, dict), f"OpenAPI reference is not an object: {ref}"
        value = resolved
    return value


def _schema_requires_client_id(schema: dict[str, Any], value: dict[str, Any]) -> bool:
    value = _resolve(schema, value)
    if "client_id" in value.get("required", ()):
        return True
    return any(
        _schema_requires_client_id(schema, item)
        for key in ("allOf", "anyOf", "oneOf")
        for item in value.get(key, ())
    )


def _operation_requires_client_id(schema: dict[str, Any], operation: dict[str, Any]) -> bool:
    for parameter in operation.get("parameters", ()):
        parameter = _resolve(schema, parameter)
        if parameter.get("name") == "client_id" and parameter.get("required"):
            return True

    request_body = operation.get("requestBody")
    if not request_body:
        return False
    request_body = _resolve(schema, request_body)
    if not request_body.get("required"):
        return False
    return any(
        _schema_requires_client_id(schema, media_type.get("schema", {}))
        for media_type in request_body.get("content", {}).values()
    )


def test_finite_ui_routes_require_client_id_in_openapi():
    """Every finite 202 UI operation declares how it identifies its live tab."""
    with TestClient(app):
        schema = app.openapi()

    finite_operations = {
        (method, path)
        for path, path_item in schema["paths"].items()
        if path.startswith("/api/ui")
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
        and "202" in operation.get("responses", {})
    }
    exceptions_in_schema = CLIENT_ID_EXCEPTIONS & finite_operations
    assert exceptions_in_schema == CLIENT_ID_EXCEPTIONS, (
        f"Remove stale client_id exceptions: {sorted(CLIENT_ID_EXCEPTIONS - finite_operations)}"
    )

    missing = sorted(
        (method, path)
        for method, path in finite_operations - CLIENT_ID_EXCEPTIONS
        if not _operation_requires_client_id(schema, schema["paths"][path][method])
    )
    assert not missing, f"Finite 202 UI operations missing required client_id: {missing}"
