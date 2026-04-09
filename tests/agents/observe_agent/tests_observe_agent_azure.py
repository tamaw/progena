"""Tests for ObserveAgent Azure-specific _post_responses behaviour."""

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
            raise httpx.HTTPStatusError(
                message=f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://test"),
                response=httpx.Response(self.status_code),
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
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    state_dir = tmp_path / "state"
    logs_dir = tmp_path / "logs"
    state_dir.mkdir()
    logs_dir.mkdir()
    return ObserveAgent(state_dir=state_dir, logs_dir=logs_dir)


async def test_post_responses_maps_model_to_deployment_for_azure(
    azure_agent: ObserveAgent, monkeypatch
) -> None:
    """Azure endpoint should receive 'deployment' instead of 'model'."""
    captured: list[dict[str, Any]] = []

    async def fake_post(url: str, *, json: dict[str, Any], **kwargs: Any) -> _FakeResponse:
        captured.append(dict(json))
        return _FakeResponse(200, {"output_text": "ok"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(fake_post, **kw))

    await azure_agent._post_responses({"model": "gpt-5-mini", "input": []})

    assert len(captured) == 1
    assert "deployment" in captured[0], "expected 'deployment' key in Azure payload"
    assert "model" not in captured[0], "expected 'model' key removed from Azure payload"
    assert captured[0]["deployment"] == "gpt-5-mini"


async def test_post_responses_logs_body_on_400(
    azure_agent: ObserveAgent, monkeypatch, caplog
) -> None:
    """A 4xx response should log the full response body before raising."""
    error_body = {"error": {"code": "DeploymentNotFound", "message": "not found"}}

    async def fake_post(url: str, *, json: dict[str, Any], **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(400, error_body)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(fake_post, **kw))

    with caplog.at_level(logging.ERROR, logger="app.agents.observe_agent.agent"), pytest.raises(httpx.HTTPStatusError):
        await azure_agent._post_responses({"model": "gpt-5-mini", "input": []})

    error_messages = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "400" in msg and "DeploymentNotFound" in msg for msg in error_messages
    ), f"Expected 400 + DeploymentNotFound in error logs, got: {error_messages}"
