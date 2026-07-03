"""Tests for the generic state tools: generate_id, delete_state."""

import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.progenitor.tools.delete_state import DeleteStateTool
from app.progenitor.tools.generate_id import GenerateIdTool
from app.progenitor.tools.protocol import ToolContext


class _FakeStateManager:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.docs: dict[str, Any] = dict(initial or {})
        self.deleted: list[str] = []

    async def get_state_document(self, name: str, default: Any = None) -> Any:
        return self.docs.get(name, default)

    async def delete_state_document(self, name: str) -> None:
        self.deleted.append(name)
        self.docs.pop(name, None)


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


# --------------------------------------------------------------------------- #
# delete_state
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_delete_state_removes_document() -> None:
    sm = _FakeStateManager({"kanban_card:c1": {"id": "c1"}})
    ctx = ToolContext(
        tool_registry=None,
        llm_client=MagicMock(_push_event=None),
        state_manager=sm,
        tool_args={"document": "kanban_card:c1"},
    )
    result = await DeleteStateTool().execute(ctx)

    assert result.success is True
    assert result.data is not None
    assert result.data["deleted"] is True
    assert result.data["action"] == "deleted"
    assert "kanban_card:c1" in sm.deleted


@pytest.mark.asyncio
async def test_delete_state_noop_when_absent() -> None:
    sm = _FakeStateManager()
    ctx = ToolContext(
        tool_registry=None,
        llm_client=MagicMock(_push_event=None),
        state_manager=sm,
        tool_args={"document": "gone"},
    )
    result = await DeleteStateTool().execute(ctx)

    assert result.success is True
    assert result.data is not None
    assert result.data["deleted"] is False
    assert result.data["action"] == "noop"

    from app.progenitor.tools.system_registry import get_system_tool

    for name in ("generate_id", "delete_state"):
        assert get_system_tool(name) is not None, name
