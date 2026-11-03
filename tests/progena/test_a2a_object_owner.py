"""A2A callbacks retain the identity of their originating object agent."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.store.memory import InMemoryStore
from langgraph_sdk import get_client

from src.progena.a2a.tasks import enqueue_completion_followup
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.runtime.http.server import ProgenaHTTPServer
from src.progena.state.manager import StateManager
from src.progena.tools.a2a_sessions import A2ASendAsyncTool
from src.progena.tools.protocol import ToolContext


@pytest.mark.asyncio
async def test_a2a_completion_resumes_owning_object_agent(monkeypatch):
    state = StateManager(runtime_store=LangGraphRuntimeStore(InMemoryStore()))
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()
    calls = []
    remote = SimpleNamespace(
        send_message=AsyncMock(
            return_value={
                "success": True,
                "result": {"kind": "task", "id": "a2a-1", "status": {"state": "submitted"}},
            }
        )
    )
    monkeypatch.setattr("src.progena.a2a.tasks.A2AClient", lambda: remote)
    registry = SimpleNamespace(
        find_by_name=lambda _name: {
            "name": "greetings",
            "transport": "http",
            "url": "http://example.test",
        }
    )

    async def owner_agent(invocation, config):
        calls.append((invocation, config))
        if len(calls) == 1:
            result = await A2ASendAsyncTool().execute(
                ToolContext(
                    tool_registry=registry,
                    llm_client=None,
                    state_manager=state,
                    state={"thread_id": "stale-main"},
                    tool_args={"agent": "greetings", "message": "hello"},
                )
            )
            assert result.success
            return {"messages": [AIMessage(content="A2A started")]}
        callback_started.set()
        await release_callback.wait()
        return {"messages": [AIMessage(content="A2A continuation received")]}

    graph = SimpleNamespace(
        object_agent_registry=SimpleNamespace(
            callables={
                "owner": RunnableLambda(owner_agent),
            }
        )
    )
    server = ProgenaHTTPServer(
        state_manager=state, port=0, loop_runner=SimpleNamespace(graph=graph)
    )
    await server.start()
    try:
        client = get_client(url=f"{server.base_url}/agent-protocol")
        thread = await client.threads.create()
        await client.runs.wait(
            thread["thread_id"], "owner", input={"messages": [{"role": "user", "content": "start"}]}
        )
        record = await state.get_a2a_task("a2a-1")
        assert record is not None
        assert record.request_metadata["thread_id"] == f"object-agent:owner:{thread['thread_id']}"
        assert record.request_metadata["owner"]["thread_id"] == thread["thread_id"]
        queues = SimpleNamespace(enqueue_input=AsyncMock(side_effect=AssertionError("wrong graph")))
        assert not await enqueue_completion_followup(state, queues, record)
        assert (await state.get_a2a_task("a2a-1")).notification_sent_at is None
        assert await enqueue_completion_followup(
            state, queues, record, object_agent_protocol=server._agent_protocol
        )
        await asyncio.wait_for(callback_started.wait(), timeout=5)
        [task] = [
            task
            for (thread_id, _), task in server._agent_protocol._tasks.items()
            if thread_id == thread["thread_id"]
        ]
        release_callback.set()
        await task
        assert (
            calls[-1][1]["configurable"]["object_agent_thread_id"]
            == record.request_metadata["thread_id"]
        )
        assert (await client.threads.get(thread["thread_id"]))["values"]["messages"][-1][
            "content"
        ] == "A2A continuation received"
        assert (await state.get_a2a_task("a2a-1")).notification_sent_at is not None
    finally:
        release_callback.set()
        await server.stop()
