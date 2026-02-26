from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.rediscover_agents import RediscoverAgentsTool


@pytest.mark.asyncio
async def test_rediscover_agents_uses_app_agents_dir_and_syncs_litellm(monkeypatch):
    tool = RediscoverAgentsTool()
    mock_registry = MagicMock()
    mock_registry.sync_agents_from_litellm = AsyncMock(return_value={"agents_seen": 1})
    mock_llm = MagicMock()

    captured = {}

    async def _fake_discover_agents(agents_dir, llm_client, registry):  # noqa: ANN001
        captured["agents_dir"] = str(agents_dir)
        captured["llm_client"] = llm_client
        captured["registry"] = registry
        return {"success": True, "agents_found": 2, "agents_registered": 2}

    monkeypatch.setattr(
        "app.progenitor.tools.agent_discovery.discover_agents",
        _fake_discover_agents,
    )

    ctx = ToolContext(
        tool_registry=mock_registry,
        llm_client=mock_llm,
        user_input="rediscover agents",
    )
    result = await tool.execute(ctx)

    assert result.success is True
    assert "/app/agents" in captured["agents_dir"].replace("\\", "/")
    assert captured["llm_client"] is mock_llm
    assert captured["registry"] is mock_registry
    mock_registry.sync_agents_from_litellm.assert_awaited_once()
    assert result.data is not None
    assert result.data["litellm_sync"] == {"agents_seen": 1}
