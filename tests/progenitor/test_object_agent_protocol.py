"""Exercise real object agents through the installed LangGraph SDK and tools."""

import asyncio
import uuid
from types import SimpleNamespace

import httpx
import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph_sdk import get_client

from app.progenitor.agent.live_async_subagents import LiveAsyncSubagentMiddleware
from app.progenitor.agent.middleware.async_subagent_tracking import AsyncSubagentTrackingMiddleware
from app.progenitor.agent.object_agent_registry import LiveSubagentMiddleware, ObjectAgentRegistry
from app.progenitor.agent.object_agents import create_object_agent_proxy
from app.progenitor.async_subagents.protocol import RUN_PREFIX, THREAD_PREFIX
from app.progenitor.runtime.http import ProgenitorHTTPServer
from app.progenitor.state.manager import StateManager
from tests.progenitor.conftest import _FakeRuntimeStore


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        result = super()._generate(*args, **kwargs)
        for generation in result.generations:
            generation.message = generation.message.model_copy(update={"id": uuid.uuid4().hex})
        return result


def manifest(name):
    return f"---\nname: {name}\ndescription: Greets the caller.\nversion: 1.0.0\nkind: object\n---\nSay hello.\n"


async def setup_registry(tmp_path):
    state = StateManager(runtime_store=_FakeRuntimeStore())
    await state.save_content_document("agent", "user/greeter/SYSTEM.md", manifest("greeter"))
    checkpointer = MemorySaver()
    backend = CompositeBackend(default=StateBackend(), routes={})

    def proxy(source):
        return create_object_agent_proxy(
            source,
            state_manager=state,
            model=Model(responses=[AIMessage(content="hello from the real agent")]),
            tools=[],
            backend=backend,
            checkpointer=checkpointer,
        )

    registry = ObjectAgentRegistry(state_manager=state, proxy_factory=proxy, native_root=tmp_path)
    await registry.refresh()
    return state, registry, backend


def server_for(state, registry):
    return ProgenitorHTTPServer(
        state_manager=state,
        port=0,
        loop_runner=SimpleNamespace(graph=SimpleNamespace(object_agent_registry=registry)),
    )


async def test_same_manifest_wait_background_history_and_checkpoint_isolation(tmp_path):
    state, registry, _ = await setup_registry(tmp_path)
    server = server_for(state, registry)
    await server.start()
    try:
        client = get_client(url=f"{server.base_url}/agent-protocol")
        thread = await client.threads.create()
        thread_id = thread["thread_id"]
        waited = await client.runs.wait(
            thread_id, "greeter", input={"messages": [{"role": "user", "content": "first"}]}
        )
        assert waited["messages"][-1]["content"] == "hello from the real agent"
        run = await client.runs.create(
            thread_id, "greeter", input={"messages": [{"role": "user", "content": "second"}]}
        )
        joined = await client.runs.join(thread_id, run["run_id"])
        assert joined["messages"][-1]["content"] == "hello from the real agent"
        assert [m["content"] for m in joined["messages"] if m["type"] == "human"] == [
            "first",
            "second",
        ]
        assert (await client.runs.get(thread_id, run["run_id"]))["status"] == "success"

        other = await client.threads.create()
        isolated = await client.runs.wait(
            other["thread_id"],
            "greeter",
            input={"messages": [{"role": "user", "content": "isolated"}]},
        )
        assert [m["content"] for m in isolated["messages"] if m["type"] == "human"] == ["isolated"]
    finally:
        await server.stop()

    # A new server reads authoritative results from the same persistent store.
    replacement = server_for(state, registry)
    await replacement.start()
    try:
        client = get_client(url=f"{replacement.base_url}/agent-protocol")
        assert (await client.runs.get(thread_id, run["run_id"]))["status"] == "success"
        assert (await client.threads.get(thread_id))["values"] == joined
    finally:
        await replacement.stop()


async def test_live_sync_and_background_tools_use_same_agent_and_tracking(tmp_path):
    state, registry, backend = await setup_registry(tmp_path)
    server = server_for(state, registry)
    await server.start()
    try:
        live_async = LiveAsyncSubagentMiddleware(registry, base_url=server.base_url)
        tracking = AsyncSubagentTrackingMiddleware(
            state_manager=state, specs_provider=live_async.descriptors
        )
        model = Model(responses=[AIMessage(content="ready")])
        graph = create_deep_agent(
            model=model,
            checkpointer=MemorySaver(),
            middleware=[tracking, LiveSubagentMiddleware(registry, backend=backend), live_async],
        )
        config = {"configurable": {"thread_id": "parent-chat"}}

        async def call(tool_name, args):
            model.i = 0
            model.responses = [
                AIMessage(content="", tool_calls=[{"name": tool_name, "args": args, "id": "call"}]),
                AIMessage(content="done"),
            ]
            return await graph.ainvoke({"messages": [HumanMessage(content="delegate")]}, config)

        sync = await call("task", {"subagent_type": "greeter", "description": "say hello"})
        assert any(
            isinstance(m, ToolMessage) and m.content == "hello from the real agent"
            for m in sync["messages"]
        )
        await call(
            "start_async_task", {"subagent_type": "greeter", "description": "say hello later"}
        )
        records = await state.get_async_subagent_tasks()
        assert len(records) == 1
        record = records[0]
        assert record.agent_name == "greeter"
        assert record.parent_thread_id == "parent-chat"
        client = get_client(url=f"{server.base_url}/agent-protocol")
        await client.runs.join(record.task_id, record.run_id)
        checked = await call("check_async_task", {"task_id": record.task_id})
        assert any(
            isinstance(m, ToolMessage) and "hello from the real agent" in m.content
            for m in checked["messages"]
        )
        assert (await state.get_async_subagent_task(record.task_id)).status == "success"

        await state.save_content_document("agent", "user/second/SYSTEM.md", manifest("second"))
        await registry.refresh()
        assert {s["name"] for s in live_async.descriptors()} == {"greeter", "second"}
        await call("start_async_task", {"subagent_type": "second", "description": "say hello"})
        assert {r.agent_name for r in await state.get_async_subagent_tasks()} == {
            "greeter",
            "second",
        }
        await state.delete_content_document("agent", "user/greeter/SYSTEM.md")
        await registry.refresh()
        assert {s["name"] for s in live_async.descriptors()} == {"second"}
        # History remains usable after deleting the definition.
        assert (await client.runs.get(record.task_id, record.run_id))["status"] == "success"
        deleted_check = await call("check_async_task", {"task_id": record.task_id})
        assert any(
            isinstance(m, ToolMessage) and "hello from the real agent" in m.content
            for m in deleted_check["messages"]
        )
        await call("start_async_task", {"subagent_type": "greeter", "description": "deleted"})
        assert len(await state.get_async_subagent_tasks()) == 2
    finally:
        await server.stop()


async def test_background_cancel_update_failure_and_missing_agent(tmp_path):
    state, registry, _ = await setup_registry(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(input):
        started.set()
        await release.wait()
        return input

    async def fail(input):
        raise RuntimeError("intentional execution failure")

    registry.callables["slow"] = RunnableLambda(slow)
    registry.callables["fail"] = RunnableLambda(fail)
    server = server_for(state, registry)
    await server.start()
    try:
        client = get_client(url=f"{server.base_url}/agent-protocol")
        thread_id = (await client.threads.create())["thread_id"]
        first = await client.runs.create(thread_id, "slow", input={"messages": []})
        await asyncio.wait_for(started.wait(), 5)
        with pytest.raises(httpx.HTTPStatusError, match="409"):
            await client.runs.create(thread_id, "slow", input={"messages": []})
        updated = await client.runs.create(
            thread_id, "slow", input={"messages": []}, multitask_strategy="interrupt"
        )
        assert (await client.runs.get(thread_id, first["run_id"]))["status"] == "cancelled"
        await client.runs.cancel(thread_id, updated["run_id"])
        assert (await client.runs.get(thread_id, updated["run_id"]))["status"] == "cancelled"

        fail_thread = (await client.threads.create())["thread_id"]
        with pytest.raises(Exception, match="intentional execution failure"):
            await client.runs.wait(fail_thread, "fail", input={"messages": []})
        with pytest.raises(httpx.HTTPStatusError, match="404"):
            await client.runs.create(fail_thread, "missing", input={"messages": []})
    finally:
        await server.stop()


async def test_restart_marks_unfinished_runs_as_failed(tmp_path):
    state, registry, _ = await setup_registry(tmp_path)
    await state._runtime_store.save_document(
        THREAD_PREFIX + "abandoned",
        {"thread_id": "abandoned", "values": {"messages": []}, "status": "busy"},
    )
    key = RUN_PREFIX + "abandoned:run"
    await state._runtime_store.save_document(
        key, {"thread_id": "abandoned", "run_id": "run", "status": "running"}
    )
    server = server_for(state, registry)
    await server.start()
    try:
        run = await state._runtime_store.peek_document(key)
        assert run["status"] == "error"
        assert "restarted" in run["error"]
    finally:
        await server.stop()


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_real_object_agent_approval_pauses_and_resumes_same_thread(tmp_path, decision):
    state = StateManager(runtime_store=_FakeRuntimeStore())
    content = manifest("guarded").replace(
        "kind: object\n",
        "kind: object\ntools: [guarded_action]\ninterrupt_on:\n  guarded_action: true\n",
    )
    await state.save_content_document("agent", "user/guarded/SYSTEM.md", content)
    calls = []

    @tool
    def guarded_action() -> str:
        """Perform the action only after approval."""
        calls.append("executed")
        return "action finished"

    model = Model(
        responses=[
            AIMessage(
                content="", tool_calls=[{"name": "guarded_action", "args": {}, "id": "guard"}]
            ),
            AIMessage(content="done"),
        ]
    )
    checkpointer = MemorySaver()

    def proxy(source):
        return create_object_agent_proxy(
            source,
            state_manager=state,
            model=model,
            tools=[guarded_action],
            backend=CompositeBackend(default=StateBackend(), routes={}),
            checkpointer=checkpointer,
        )

    registry = ObjectAgentRegistry(state_manager=state, proxy_factory=proxy, native_root=tmp_path)
    await registry.refresh()
    server = server_for(state, registry)
    await server.start()
    try:
        client = get_client(url=f"{server.base_url}/agent-protocol")
        thread_id = (await client.threads.create())["thread_id"]
        run = await client.runs.create(
            thread_id, "guarded", input={"messages": [{"role": "user", "content": "act"}]}
        )
        paused = await client.runs.join(thread_id, run["run_id"])
        assert paused["__interrupt__"][0]["value"]["action_requests"][0]["name"] == "guarded_action"
        assert (await client.runs.get(thread_id, run["run_id"]))["status"] == "interrupted"
        assert calls == []
        finished = await client.runs.wait(
            thread_id, "guarded", command={"resume": {"decisions": [{"type": decision}]}}
        )
        assert not finished.get("__interrupt__")
        assert finished["messages"][-1]["content"] == "done"
        assert calls == (["executed"] if decision == "approve" else [])
    finally:
        await server.stop()


@pytest.mark.parametrize(
    "response_type, expected_status", [("number", "success"), ("string", "error")]
)
async def test_routine_background_continues_before_agent_and_validates_eventual_result(
    tmp_path, response_type, expected_status
):
    from app.progenitor.async_subagents.tasks import (
        create_agent_background_runner,
        refresh_tracked_async_subagent_task,
    )
    from app.progenitor.routines.factory import compile_routine_graph
    from app.progenitor.routines.graph import validate_routine_graph_payload

    state, registry, _ = await setup_registry(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def gated(_):
        entered.set()
        await release.wait()
        return {"messages": [AIMessage(content=[{"type": "text", "text": '{"answer": 42}'}])]}

    registry.callables["greeter"] = RunnableLambda(gated)
    server = server_for(state, registry)
    await server.start()
    try:
        graph = validate_routine_graph_payload(
            {
                "id": "background-check",
                "name": "Background check",
                "description": "",
                "version": "2.0.0",
                "entry": "delegate",
                "nodes": [
                    {
                        "id": "delegate",
                        "kind": "agent",
                        "output_key": "task",
                        "config": {
                            "agent_name": "greeter",
                            "prompt_template": "hello",
                            "execution": "background",
                            "response_schema": {
                                "type": "object",
                                "required": ["answer"],
                                "properties": {"answer": {"type": response_type}},
                            },
                        },
                    },
                    {
                        "id": "after",
                        "kind": "eval",
                        "output_key": "after",
                        "config": {"expression": "task.run_id"},
                    },
                ],
                "edges": [{"from": "delegate", "to": "after", "level": "normal"}],
            }
        )
        runnable = compile_routine_graph(
            graph,
            model=Model(responses=[]),
            agent_registry=registry.callables,
            agent_background_runner=create_agent_background_runner(state, server.base_url),
        )
        result = await asyncio.wait_for(runnable.ainvoke({"data": {}}), 5)
        await asyncio.wait_for(entered.wait(), 5)
        task = result["data"]["task"]
        assert result["data"]["after"] == task["run_id"]
        client = get_client(url=f"{server.base_url}/agent-protocol")
        assert (await client.runs.get(task["thread_id"], task["run_id"]))["status"] == "running"
        tracked = await state.get_async_subagent_task(task["task_id"])
        assert tracked.source == "routine_agent"
        assert tracked.request_metadata["node_id"] == "delegate"
        release.set()
        await client.runs.join(task["thread_id"], task["run_id"])
        finished = await client.runs.get(task["thread_id"], task["run_id"])
        refreshed = await refresh_tracked_async_subagent_task(state, task["task_id"])
        assert refreshed.status == expected_status
        if expected_status == "success":
            assert refreshed.summary == '{"answer": 42}'
        assert finished["status"] == expected_status
        if expected_status == "error":
            assert "schema validation" in finished["error"]
        else:
            assert finished["values"]["response"] == {"answer": 42}
    finally:
        release.set()
        await server.stop()


@pytest.mark.parametrize("mode", ["wait", "background"])
async def test_actual_object_agent_uses_profile_override_for_both_modes(
    tmp_path, monkeypatch, mode
):
    state, registry, _ = await setup_registry(tmp_path)
    await state.save_model_profiles_config(
        {"selected": {"provider": "azure_openai", "deployment": "profile-greeter"}}
    )
    configs = []

    def selected_model(config, *, profile_name=None):
        configs.append(config.model_default)
        return Model(responses=[AIMessage(content="profile greeting")])

    monkeypatch.setattr("app.progenitor.agent.adapters.create_langchain_model", selected_model)
    server = server_for(state, registry)
    await server.start()
    try:
        client = get_client(url=f"{server.base_url}/agent-protocol")
        thread = await client.threads.create()
        kwargs = {
            "input": {"messages": [{"role": "user", "content": "hello"}]},
            "config": {"configurable": {"model_profile": "selected"}},
        }
        if mode == "wait":
            result = await client.runs.wait(thread["thread_id"], "greeter", **kwargs)
        else:
            run = await client.runs.create(thread["thread_id"], "greeter", **kwargs)
            assert run["model_profile"] == "selected"
            result = await client.runs.join(thread["thread_id"], run["run_id"])
        assert result["messages"][-1]["content"] == "profile greeting"
        assert configs == ["profile-greeter"]
        default_thread = await client.threads.create()
        default = await client.runs.wait(
            default_thread["thread_id"],
            "greeter",
            input={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert default["messages"][-1]["content"] == "hello from the real agent"
        assert configs == ["profile-greeter"]
        await state.save_content_document(
            "agent",
            "user/greeter/SYSTEM.md",
            manifest("greeter").replace(
                "version: 1.0.0", "version: 1.0.0\nmodel: manifest-deployment"
            ),
        )
        overridden_thread = await client.threads.create()
        overridden = await client.runs.wait(overridden_thread["thread_id"], "greeter", **kwargs)
        assert overridden["messages"][-1]["content"] == "profile greeting"
        assert configs == ["profile-greeter", "profile-greeter"]

    finally:
        await server.stop()
