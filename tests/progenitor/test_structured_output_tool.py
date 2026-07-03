"""Tests for the generic structured_output tool."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.structured_output import StructuredOutputTool


@pytest.fixture
def schema_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "tags.schema.v1.json").write_text(
        json.dumps({"type": "array", "items": {"type": "string"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROGENITOR_SCHEMAS_DIR", str(schema_dir))
    return schema_dir


@pytest.mark.asyncio
async def test_structured_output_inline_schema_returns_value() -> None:
    llm = MagicMock()
    llm.chat_structured_json = AsyncMock(return_value={"sentiment": "positive"})

    ctx = ToolContext(
        tool_registry=None,
        llm_client=llm,
        tool_args={
            "instruction": "classify the sentiment",
            "json_schema": {
                "type": "object",
                "properties": {"sentiment": {"type": "string"}},
                "required": ["sentiment"],
            },
            "input": "I love this!",
        },
    )

    result = await StructuredOutputTool().execute(ctx)

    assert result.success is True
    assert result.data is not None
    assert result.data["value"] == {"sentiment": "positive"}
    assert result.data["schema"] == "inline"
    # Input is threaded in as context; nothing is persisted.
    _, kwargs = llm.chat_structured_json.call_args
    assert "I love this!" in kwargs["prompt"]

    from app.progenitor.tools.system_registry import get_system_tool

    tool = get_system_tool("structured_output")
    assert tool is not None
    assert tool.args_model.__name__ == "StructuredOutputArgs"


@pytest.mark.asyncio
async def test_structured_output_named_schema(schema_dir: Path) -> None:
    llm = MagicMock()
    llm.chat_structured_json = AsyncMock(return_value={"items": ["a", "b"]})

    ctx = ToolContext(
        tool_registry=None,
        llm_client=llm,
        tool_args={"instruction": "extract tags", "schema_file": "tags", "input": "a b"},
    )

    result = await StructuredOutputTool().execute(ctx)

    assert result.success is True
    assert result.data is not None
    assert result.data["value"] == ["a", "b"]
    assert result.data["schema"] == "tags"


@pytest.mark.asyncio
async def test_structured_output_requires_exactly_one_schema_source() -> None:
    llm = MagicMock()
    llm.chat_structured_json = AsyncMock()

    # Neither provided.
    ctx = ToolContext(
        tool_registry=None,
        llm_client=llm,
        tool_args={"instruction": "do it"},
    )
    result = await StructuredOutputTool().execute(ctx)
    assert result.success is False
    assert result.error is not None and "exactly one" in result.error

    # Both provided.
    ctx = ToolContext(
        tool_registry=None,
        llm_client=llm,
        tool_args={
            "instruction": "do it",
            "schema_file": "tags",
            "json_schema": {"type": "object"},
        },
    )
    result = await StructuredOutputTool().execute(ctx)
    assert result.success is False
    assert result.error is not None and "exactly one" in result.error
    llm.chat_structured_json.assert_not_called()
