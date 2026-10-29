"""ACP callbacks must resume their owning object-agent protocol thread."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.store.memory import InMemoryStore
from langgraph_sdk import get_client

from src.progena.acp.models import ACPTaskStatus
from src.progena.acp.tasks import enqueue_acp_completion_followup
from src.progena.async_subagents.protocol import RUN_PREFIX
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.runtime.http.server import ProgenaHTTPServer
from src.progena.tools.acp_sessions import ACPStartTool
from src.progena.tools.protocol import ToolContext

pytestmark = pytest.mark.asyncio


class _ACPRegistry:
    def find_by_name(self, _name: str) -> dict[str, Any]:
        return {
            "name": "Claude",
            "transport": "acp",
            "acp": {"host": "claude", "port": 3001, "cwd": "/workspace"},
        }


class _UnusedQueues:
    async def enqueue_input(self, *_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("object-agent callbacks must not use the main heartbeat queue")


async def test_acp_callback_resumes_owning_object_agent_protocol_thread() -> None:
    from src.progena.state.manager import StateManager

    state = StateManager(runtime_store=LangGraphRuntimeStore(InMemoryStore()))
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()
    calls: list[dict[str, Any]] = []
    task_ids: list[str] = []

    async def owner_agent(invocation: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        calls.append(config)
        if len(calls) == 1:
            result = await ACPStartTool().execute(
                ToolContext(
                    tool_registry=_ACPRegistry(),
                    llm_client=None,
                    state_manager=state,
                    state={"thread_id": "progena_main"},
                    tool_args={"agent": "Claude", "prompt": "inspect this"},
                )
            )
            assert result.success and result.data is not None
            task_ids.append(str(result.data["task_id"]))
            return {"messages": [AIMessage(content="ACP started")]}
        callback_started.set()
        await release_callback.wait()
        return {"messages": [AIMessage(content="ACP continuation received")]}

    registry = SimpleNamespace(callables={"owner": RunnableLambda(owner_agent)})
    loop_runner = SimpleNamespace(graph=SimpleNamespace(object_agent_registry=registry))
    server = ProgenaHTTPServer(state_manager=state, port=0, loop_runner=loop_runner)
    await server.start()
    try:
        client = get_client(url=f"{server.base_url}/agent-protocol")
        thread = await client.threads.create()
        await client.runs.wait(
            thread["thread_id"],
            "owner",
            input={"messages": [{"role": "user", "content": "start"}]},
        )
        [record] = await state.get_acp_tasks()
        assert record.task_id == task_ids[0]
        owner = record.request_metadata["owner"]
        assert owner == {
            "protocol": "object_agent",
            "assistant_id": "owner",
            "thread_id": thread["thread_id"],
            "run_id": owner["run_id"],
            "checkpoint_thread_id": f"object-agent:owner:{thread['thread_id']}",
        }

        record.status = ACPTaskStatus.COMPLETED
        delivered = await enqueue_acp_completion_followup(
            state,
            _UnusedQueues(),
            record,
            object_agent_protocol=server._agent_protocol,
            event_identity="terminal:0:completed",
        )
        assert delivered
        await asyncio.wait_for(callback_started.wait(), timeout=5)
        continuation_tasks = [
            task
            for (protocol_thread_id, _), task in server._agent_protocol._tasks.items()
            if protocol_thread_id == thread["thread_id"]
        ]
        assert len(continuation_tasks) == 1
        release_callback.set()
        await continuation_tasks[0]

        checkpoint_thread = f"object-agent:owner:{thread['thread_id']}"
        assert calls[-1]["configurable"]["object_agent_thread_id"] == checkpoint_thread
        protocol_thread = await client.threads.get(thread["thread_id"])
        assert protocol_thread["values"]["messages"][-1]["content"] == "ACP continuation received"
        runs = await state._runtime_store.list_documents(RUN_PREFIX)
        assert sum(run["thread_id"] == thread["thread_id"] for _, run in runs) == 2
    finally:
        await server.stop()
