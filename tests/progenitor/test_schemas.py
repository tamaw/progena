"""Tests for schema loading helpers (app.progenitor.schemas)."""

import json
from pathlib import Path

import pytest

from app.progenitor import schemas


@pytest.fixture
def schema_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "preferences.schema.v1.json").write_text(
        json.dumps({"type": "object", "properties": {"timezone": {"type": "string"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROGENITOR_SCHEMAS_DIR", str(schema_dir))
    return schema_dir


def test_load_schema_resolves_latest_version_and_pins_exact_filename(schema_dir: Path) -> None:
    # A higher version present should win over v1 for the bare logical name...
    (schema_dir / "preferences.schema.v2.json").write_text(
        json.dumps({"type": "object", "properties": {"locale": {"type": "string"}}}),
        encoding="utf-8",
    )
    assert "locale" in schemas.load_schema("preferences")["properties"]
    # ...but an exact filename pins that specific version.
    assert schemas.load_schema("preferences.schema.v1.json")["properties"] == {
        "timezone": {"type": "string"}
    }
    assert set(schemas.list_schemas()) == {
        "preferences.schema.v1.json",
        "preferences.schema.v2.json",
    }


def test_load_schema_missing_lists_available(schema_dir: Path) -> None:
    with pytest.raises(FileNotFoundError) as exc:
        schemas.load_schema("missing")
    assert "preferences.schema.v1.json" in str(exc.value)


@pytest.mark.parametrize(
    "traversal",
    [
        "../secrets.json",
        "../../etc/passwd.json",
        "a/b.json",
        "a\\b.json",
        "..",
        ".",
        "",
    ],
)
def test_resolve_schema_path_rejects_non_bare_filenames(schema_dir: Path, traversal: str) -> None:
    """schema_file is agent-controlled; only a bare filename may be resolved."""
    with pytest.raises(ValueError, match="bare filename"):
        schemas.resolve_schema_path(traversal)


def test_resolve_schema_path_cannot_escape_schema_root(tmp_path: Path, schema_dir: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"secret": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="bare filename"):
        schemas.load_schema(f"../{outside.name}")


def test_mcp_server_entry_schema_accepts_url_or_endpoint() -> None:
    """validate_mcp_document and mcp/loader.py both accept 'endpoint' as a url alias."""
    import jsonschema

    schema = schemas.load_schema("mcp_server_entry")
    jsonschema.validate({"url": "https://example.com/mcp"}, schema)
    jsonschema.validate({"endpoint": "https://example.com/mcp"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"transport": "http"}, schema)


def test_workflow_node_user_and_code_schemas_match_definition_shapes() -> None:
    """node_user/node_code standalone schemas exist and accept the same shapes
    the inline 'user'/'code' oneOf branches in definition.workflow.schema.v1.json do."""
    import jsonschema

    user_schema = schemas.load_schema("node_user.workflow")
    jsonschema.validate(
        {
            "kind": "user",
            "title": "Notify",
            "message_type": "notify_user",
            "message": "The workflow finished.",
            "notification_title": "Workflow done",
            "level": "success",
        },
        user_schema,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"kind": "user", "title": "Notify"}, user_schema)

    code_schema = schemas.load_schema("node_code.workflow")
    jsonschema.validate(
        {"kind": "code", "title": "Run", "language": "python", "content": "print(1)"},
        code_schema,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"kind": "code", "title": "Run", "language": "python"}, code_schema)


def test_shipped_schemas_are_valid_json_schema() -> None:
    """Every schema checked into data/schemas/ must be a valid JSON Schema."""
    import jsonschema

    # No PROGENITOR_SCHEMAS_DIR override here: load the real shipped schemas.
    names = schemas.list_schemas()
    assert names, "expected shipped schemas under data/schemas/"
    for name in names:
        jsonschema.Draft7Validator.check_schema(schemas.load_schema(name))
