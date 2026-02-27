from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.tools.mcp import A2AClient


@pytest.mark.asyncio
async def test_call_agent_uses_litellm_a2a_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json.return_value = [
        {
            "agent_id": "agent-123",
            "agent_name": "builder_agent",
            "agent_card_params": {"name": "Builder Agent", "url": "http://progenitor-agent-builder_agent:8107"},
        }
    ]

    invoke_resp = MagicMock()
    invoke_resp.status_code = 200
    invoke_resp.json.return_value = {"jsonrpc": "2.0", "id": "1", "result": {"ok": True}}

    mock_client = AsyncMock()
    mock_client.get.return_value = list_resp
    mock_client.post.return_value = invoke_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr("app.progenitor.tools.mcp.httpx.AsyncClient", lambda *a, **k: mock_client)

    cfg = {
        "name": "builder_agent",
        "transport": "http",
        "endpoint": "http://progenitor-agent-builder_agent:8107",
        "status": "running",
    }
    result = await A2AClient().call_agent(cfg, "build hello world")

    assert result["success"] is True
    assert mock_client.post.call_args.args[0] == "http://progenitor-agent-builder_agent:8107"


@pytest.mark.asyncio
async def test_call_agent_uses_litellm_without_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json.return_value = [
        {
            "agent_id": "agent-123",
            "agent_name": "builder_agent",
            "agent_card_params": {"name": "Builder Agent", "url": "http://progenitor-agent-builder_agent:8107"},
        }
    ]

    invoke_resp = MagicMock()
    invoke_resp.status_code = 200
    invoke_resp.json.return_value = {"jsonrpc": "2.0", "id": "1", "result": {"ok": True}}

    mock_client = AsyncMock()
    mock_client.get.return_value = list_resp
    mock_client.post.return_value = invoke_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr("app.progenitor.tools.mcp.httpx.AsyncClient", lambda *a, **k: mock_client)

    cfg = {"name": "builder_agent", "transport": "http", "status": "running"}
    result = await A2AClient().call_agent(cfg, "build hello world")

    assert result["success"] is True
    assert cfg["litellm_agent_id"] == "agent-123"
    assert mock_client.post.call_args.args[0] == "http://progenitor-agent-builder_agent:8107"


@pytest.mark.asyncio
async def test_get_agent_card_uses_litellm_agent_registry(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    expected_card = {"name": "Builder Agent", "description": "Builds agents"}
    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json.return_value = [
        {
            "agent_id": "agent-123",
            "agent_name": "builder_agent",
            "agent_card_params": expected_card,
        }
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = list_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr("app.progenitor.tools.mcp.httpx.AsyncClient", lambda *a, **k: mock_client)

    cfg = {"name": "builder_agent", "transport": "http", "endpoint": "http://progenitor-agent-builder_agent:8107"}
    card = await A2AClient().get_agent_card(cfg)

    assert card == expected_card
    assert cfg["litellm_agent_id"] == "agent-123"


@pytest.mark.asyncio
async def test_call_agent_prefers_direct_endpoint_over_litellm_lookup(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")

    client = A2AClient()
    direct_call = AsyncMock(return_value={"success": True, "result": {"ok": True}})
    litellm_call = AsyncMock(return_value={"success": False, "error": "should not be called"})

    monkeypatch.setattr(client, "_call_http_agent", direct_call)
    monkeypatch.setattr(client, "_call_litellm_agent", litellm_call)

    cfg = {
        "name": "builder_agent",
        "transport": "http",
        "endpoint": "http://progenitor-agent-builder_agent:8107",
        "status": "running",
    }
    result = await client.call_agent(cfg, "build hello world")

    assert result["success"] is True
    direct_call.assert_awaited_once()
    litellm_call.assert_not_awaited()
