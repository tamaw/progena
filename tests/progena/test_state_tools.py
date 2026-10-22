"""Tests for the generic state tools: generate_id."""

import re

import pytest

from src.progena.tools.generate_id import GenerateIdTool
from src.progena.tools.protocol import ToolContext

# --------------------------------------------------------------------------- #
# generate_id
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_id_formats_and_sanitizes_prefix() -> None:
    ctx = ToolContext(tool_registry=None, llm_client=None, tool_args={"prefix": "My Cron!!"})
    result = await GenerateIdTool().execute(ctx)

    assert result.success is True
    assert result.data is not None
    assert result.data["prefix"] == "my_cron"
    assert re.fullmatch(r"my_cron_[0-9a-f]{12}", result.data["id"])
    assert result.data["ids"] == [result.data["id"]]


@pytest.mark.asyncio
async def test_generate_id_count_and_uniqueness() -> None:
    ctx = ToolContext(tool_registry=None, llm_client=None, tool_args={"prefix": "card", "count": 5})
    result = await GenerateIdTool().execute(ctx)

    assert result.data is not None
    ids = result.data["ids"]
    assert len(ids) == 5
    assert len(set(ids)) == 5
    assert all(i.startswith("card_") for i in ids)
