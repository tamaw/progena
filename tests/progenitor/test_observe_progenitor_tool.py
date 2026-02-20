import os
from unittest.mock import MagicMock

import pytest

from app.progenitor.tools.observe_progenitor import ObserveProgenitorTool
from app.progenitor.tools.protocol import ToolContext


@pytest.mark.asyncio
async def test_observe_progenitor_tool_requires_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("PROGENITOR_OBSERVE_AGENT_ENDPOINT", raising=False)

    tool = ObserveProgenitorTool()
    tool_registry = MagicMock()
    tool_registry.find_by_name = MagicMock(return_value=None)

    ctx = ToolContext(
        tool_registry=tool_registry,
        llm_client=MagicMock(),
        state=None,
        state_manager=None,
        user_input="why did you error last time",
        tool_args={"request": "why did you error last time"},
    )

    res = await tool.execute(ctx)
    assert res.success is False
    assert "PROGENITOR_OBSERVE_AGENT_ENDPOINT" in (res.error or "")

