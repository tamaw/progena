"""Tests for the json_to_yaml tool."""

import pytest

from src.progenitor.tools.json_to_yaml import JsonToYamlTool
from src.progenitor.tools.protocol import ToolContext


@pytest.mark.asyncio
async def test_plain_yaml_dump_without_body_field() -> None:
    ctx = ToolContext(
        tool_registry=None,
        llm_client=None,
        tool_args={"data": {"title": "Short title", "labels": ["a", "b"]}},
    )

    result = await JsonToYamlTool().execute(ctx)

    assert result.success is True
    assert result.data is not None
    content = result.data["content"]
    assert "---" not in content
    assert "title: Short title" in content
    assert "labels" in content


@pytest.mark.asyncio
async def test_frontmatter_with_body_field() -> None:
    ctx = ToolContext(
        tool_registry=None,
        llm_client=None,
        tool_args={
            "data": {
                "title": "Fix the thing",
                "summary": "One sentence about the thing.",
                "column_id": "backlog",
                "description": "Full body text.\nSecond line.",
            },
            "body_field": "description",
        },
    )

    result = await JsonToYamlTool().execute(ctx)

    assert result.success is True
    content = result.data["content"]
    assert content.startswith("---\n")
    assert "description:" not in content  # pulled out of frontmatter
    assert "title: Fix the thing" in content
    assert "column_id: backlog" in content
    assert content.strip().endswith("Full body text.\nSecond line.")


@pytest.mark.asyncio
async def test_frontmatter_with_missing_body_field_omits_trailing_body() -> None:
    ctx = ToolContext(
        tool_registry=None,
        llm_client=None,
        tool_args={"data": {"title": "No body here"}, "body_field": "description"},
    )

    result = await JsonToYamlTool().execute(ctx)

    assert result.success is True
    content = result.data["content"]
    assert content == "---\ntitle: No body here\n---\n"


@pytest.mark.asyncio
async def test_rejects_non_object_data() -> None:
    ctx = ToolContext(
        tool_registry=None,
        llm_client=None,
        tool_args={"data": "not an object"},
    )

    result = await JsonToYamlTool().execute(ctx)

    assert result.success is False
    assert result.error is not None and "must be a JSON object" in result.error
