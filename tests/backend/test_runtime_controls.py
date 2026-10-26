"""HTTP contract tests for backend routine-run controls."""

from __future__ import annotations

import httpx
import pytest

from src.backend.config import ChatServerConfig
from src.backend.integrations.runtime import RuntimeControlError, SSEProgenaClient


@pytest.mark.asyncio
async def test_control_client_preserves_runtime_conflict_status():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "No active routine run owns this identity"})

    client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    client._started = True
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(RuntimeControlError) as raised:
            await client.control_routine_run(
                "alpha",
                thread_id="routine:alpha:revision:session",
                request_id="run-attempt",
                action="cancel",
            )
    finally:
        await client.stop()

    assert raised.value.status_code == 409
    assert str(raised.value) == "No active routine run owns this identity"
