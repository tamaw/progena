from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langgraph.errors import GraphInterrupt

from src.progenitor.agent.middleware import ToolErrorMiddleware


def _request() -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "task", "args": {}, "id": "tc_task"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )


async def test_failing_tool_becomes_redacted_error_result():
    async def handler(_request):
        raise PermissionError("cannot create home; key sk-abcdefghijklmnopqrstuvwxyz123456")

    result = await ToolErrorMiddleware().awrap_tool_call(_request(), handler)

    assert result.status == "error"
    assert result.tool_call_id == "tc_task"
    assert "PermissionError: cannot create home" in result.content
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result.content


async def test_interrupts_still_propagate():
    async def handler(_request):
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        await ToolErrorMiddleware().awrap_tool_call(_request(), handler)


def test_sync_path_matches_async():
    def failing(_request):
        raise RuntimeError("boom")

    def interrupting(_request):
        raise GraphInterrupt(())

    assert ToolErrorMiddleware().wrap_tool_call(_request(), failing).status == "error"
    with pytest.raises(GraphInterrupt):
        ToolErrorMiddleware().wrap_tool_call(_request(), interrupting)
