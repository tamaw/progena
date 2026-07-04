from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agents.observe_agent.agent import ObserveAgent


class _FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://test")
            raise httpx.HTTPStatusError(
                message=f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )


class _FakeAsyncClient:
    """Async context manager wrapping a configurable post callable."""

    def __init__(self, post_fn, **kwargs: Any) -> None:
        self._post_fn = post_fn

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def post(self, url: str, *, json: dict[str, Any], **kwargs: Any) -> _FakeResponse:
        return await self._post_fn(url, json=json, **kwargs)


@pytest.fixture()
def azure_agent(monkeypatch, tmp_path: Path) -> ObserveAgent:
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://contoso.internal/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    state_dir = tmp_path / "state"
    logs_dir = tmp_path / "logs"
    state_dir.mkdir()
    logs_dir.mkdir()
    return ObserveAgent(state_dir=state_dir, logs_dir=logs_dir)


@pytest.mark.asyncio
async def test_observe_agent_uses_llm_and_tools(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    logs_dir = tmp_path / "logs"
    state_dir.mkdir()
    logs_dir.mkdir()

    # health is a Postgres document now (no file); build_state.json is still a
    # real on-disk file this generic tool can read, so it exercises the same
    # diagnostic-scenario shape without relying on a file that no longer exists.
    (state_dir / "build_state.json").write_text(
        json.dumps({"status": "error", "error": "boom", "cycle_count": 12})
    )
    (logs_dir / "decisions.jsonl").write_text(
        json.dumps({"event": "planning_error", "error": "something broke"}) + "\n"
    )

    class FakeObserveAgent(ObserveAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls = 0

        async def _post_responses(self, _payload):  # type: ignore[override]
            self.calls += 1
            if self.calls == 1:
                return {
                    "output_text": "",
                    "output": [
                        {
                            "type": "tool_call",
                            "call_id": "1",
                            "name": "read_state_json",
                            "arguments": json.dumps({"filename": "build_state.json"}),
                        }
                    ],
                }
            return {"output_text": "Progenitor is in error because build_state.json reports boom."}

    agent = FakeObserveAgent(state_dir=state_dir, logs_dir=logs_dir)
    text = await agent.process("why did you error last time", {})
    assert "build_state.json" in agent.tool_read_state_json("build_state.json")["path"]
    assert "boom" in text.lower()


def test_observe_agent_accepts_full_responses_base_url(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "AZURE_OPENAI_BASE_URL",
        "https://example.openai.azure.com/openai/v1/responses?api-version=preview",
    )
    agent = ObserveAgent(state_dir=tmp_path / "state", logs_dir=tmp_path / "logs")
    assert (
        agent._responses_endpoint()
        == "https://example.openai.azure.com/openai/v1/responses?api-version=preview"
    )


def test_observe_agent_tool_defs_match_responses_api_schema(tmp_path: Path) -> None:
    agent = ObserveAgent(state_dir=tmp_path / "state", logs_dir=tmp_path / "logs")
    tools = agent._tool_defs()

    assert tools
    for tool in tools:
        assert tool["type"] == "function"
        assert "function" not in tool
        assert isinstance(tool.get("name"), str) and tool["name"]
        assert isinstance(tool.get("description"), str) and tool["description"]
        assert tool.get("strict") is True
        parameters = tool.get("parameters")
        assert isinstance(parameters, dict)
        assert parameters.get("type") == "object"
        assert parameters.get("additionalProperties") is False
        assert parameters.get("required") == list((parameters.get("properties") or {}).keys())


@pytest.mark.asyncio
async def test_post_responses_keeps_model_for_azure_custom_domains(
    azure_agent: ObserveAgent, monkeypatch
) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_post(url: str, *, json: dict[str, Any], **kwargs: Any) -> _FakeResponse:
        captured.append(dict(json))
        return _FakeResponse(200, {"output_text": "ok"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(fake_post, **kw))

    await azure_agent._post_responses({"model": "gpt-5-mini", "input": []})

    assert len(captured) == 1
    assert captured[0]["model"] == "gpt-5-mini"
    assert "deployment" not in captured[0]


@pytest.mark.asyncio
async def test_post_responses_logs_body_on_400(
    azure_agent: ObserveAgent, monkeypatch, caplog
) -> None:
    error_body = {"error": {"code": "DeploymentNotFound", "message": "not found"}}

    async def fake_post(url: str, *, json: dict[str, Any], **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(400, error_body)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(fake_post, **kw))

    with (
        caplog.at_level(logging.ERROR, logger="app.agents.observe_agent.agent"),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await azure_agent._post_responses({"model": "gpt-5-mini", "input": []})

    error_messages = [
        record.message for record in caplog.records if record.levelno >= logging.ERROR
    ]
    assert any("400" in message and "DeploymentNotFound" in message for message in error_messages)
