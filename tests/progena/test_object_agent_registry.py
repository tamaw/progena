"""Postgres-backed live delegation exercised through the installed SDK."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from src.progena.agent.factory import _create_javascript_middleware
from src.progena.agent.object_agents.registry import (
    LiveSubagentMiddleware,
    ObjectAgentRegistry,
    RoutineSubagentRegistry,
)
from src.progena.agent.subagents.descriptors import create_progena_async_subagents
from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import validate_routine_graph_payload


def manifest(name, description="Does useful work."):
    return (
        f"---\nname: {name}\ndescription: {description}\nversion: 1.0.0\nkind: object\n---\nHelp.\n"
    )


class Model(FakeMessagesListChatModel):
    seen_tools: list[list[str]] = Field(default_factory=list)
    task_descriptions: list[str] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.seen_tools.append([tool.name for tool in tools])
        self.task_descriptions.extend(tool.description for tool in tools if tool.name == "task")
        return self


def registry_for(documents, proxy_factory=None, reserved_names=()):
    sm = SimpleNamespace(
        list_content_documents=AsyncMock(side_effect=lambda _kind: list(documents.items()))
    )

    def proxy(source):
        return {
            "name": source.name,
            "description": source.manifest.description,
            "runnable": RunnableLambda(
                lambda state: {"messages": [AIMessage(content=source.name)]}
            ),
        }

    return ObjectAgentRegistry(
        state_manager=sm, proxy_factory=proxy_factory or proxy, reserved_names=reserved_names
    )


def routines_for(graphs):
    """A routine registry publishing one echo subagent per stored graph."""
    sm = SimpleNamespace(list_routine_graphs=AsyncMock(side_effect=lambda: list(graphs)))

    def build(graph_doc):
        name = graph_doc["id"]
        return {
            "name": name,
            "description": f"Run {name}.",
            "runnable": RunnableLambda(lambda state: {"messages": [AIMessage(content=name)]}),
        }

    return RoutineSubagentRegistry(state_manager=sm, build=build)


async def test_create_delete_rename_plugin_task_and_routine_map_without_restart():
    documents = {}
    registry = registry_for(documents)
    await registry.refresh()
    callables = registry.callables
    middleware = LiveSubagentMiddleware(registry, backend=lambda runtime: None)
    model = Model(responses=[AIMessage(content="initial")])
    checkpointer = MemorySaver()
    graph = create_deep_agent(
        model=model,
        middleware=[middleware],
        checkpointer=checkpointer,
        subagents=create_progena_async_subagents("http://unused.test"),
    )
    config = {"configurable": {"thread_id": "live-registration"}}
    await graph.ainvoke({"messages": [HumanMessage(content="initial")]}, config)

    async def delegate(name):
        model.i = 0
        model.responses = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "work", "subagent_type": name},
                        "id": "delegate",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
        return await graph.ainvoke({"messages": [HumanMessage(content="delegate")]}, config)

    for path, name in [
        ("user/helper/SYSTEM.md", "helper"),
        ("market__plugin/helper/SYSTEM.md", "plugin-helper"),
    ]:
        documents[path] = manifest(name)
        await registry.refresh()
        assert registry.callables is callables
        assert (await callables[name].ainvoke({"messages": []}))["messages"][0].content == name
        result = await delegate(name)
        assert any(isinstance(m, ToolMessage) and m.content == name for m in result["messages"])
        assert name in middleware._snapshot().tools[0].description

    documents["user/helper/SYSTEM.md"] = manifest("renamed", "New description.")
    await registry.refresh()
    assert "helper" not in callables
    assert "renamed" in callables
    assert "New description." in middleware._snapshot().tools[0].description
    assert any(
        isinstance(m, ToolMessage) and m.content == "renamed"
        for m in (await delegate("renamed"))["messages"]
    )
    documents.clear()
    await registry.refresh()
    assert not callables
    result = await delegate("renamed")
    assert any(isinstance(m, ToolMessage) and m.status == "error" for m in result["messages"])
    assert graph.checkpointer is checkpointer
    assert any(m.content == "initial" for m in result["messages"])
    assert all(
        {
            "start_async_task",
            "check_async_task",
            "cancel_async_task",
            "update_async_task",
            "list_async_tasks",
        }
        <= set(names)
        for names in model.seen_tools
    )


async def test_general_purpose_task_coexists_with_live_objects_and_routines():
    registry = registry_for({"user/helper/SYSTEM.md": manifest("helper")})
    await registry.refresh()
    routines = routines_for([{"id": "routine-demo"}])
    await routines.refresh()
    middleware = LiveSubagentMiddleware(registry, routines=routines, backend=None)
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "help", "subagent_type": "general-purpose"},
                        "id": "gp",
                    }
                ],
            ),
            AIMessage(content="general-purpose result"),
            AIMessage(content="parent done"),
        ]
    )
    graph = create_deep_agent(model=model, middleware=[middleware])
    result = await graph.ainvoke({"messages": [HumanMessage(content="delegate")]})
    assert any(
        isinstance(m, ToolMessage) and m.content == "general-purpose result"
        for m in result["messages"]
    )
    assert all(
        name in model.task_descriptions[0] for name in ("general-purpose", "helper", "routine-demo")
    )


async def test_routine_written_in_the_same_turn_is_runnable():
    """Progena writes a routine, then runs it before any change notification lands."""
    registry = registry_for({})
    await registry.refresh()
    graphs: list[dict] = []
    routines = routines_for(graphs)
    await routines.refresh()
    middleware = LiveSubagentMiddleware(registry, routines=routines, backend=None)
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "{}", "subagent_type": "routine_new"},
                        "id": "run",
                    }
                ],
            ),
            AIMessage(content="parent done"),
        ]
    )
    graph = create_deep_agent(model=model, middleware=[middleware])
    graphs.append({"id": "routine_new"})

    result = await graph.ainvoke({"messages": [HumanMessage(content="run it")]})

    assert any(
        isinstance(m, ToolMessage) and m.content == "routine_new" for m in result["messages"]
    )


async def test_task_injected_after_compile_without_registered_task_tool():
    documents = {}
    registry = registry_for(documents)
    middleware = LiveSubagentMiddleware(registry, backend=None)
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "work", "subagent_type": "helper"},
                        "id": "dynamic",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    # The underlying LangChain graph has no statically registered task tool;
    # ToolNode passes request.tool=None to the live middleware for rebinding.
    graph = create_agent(model=model, middleware=[middleware])
    documents["user/helper/SYSTEM.md"] = manifest("helper")
    await registry.refresh()
    result = await graph.ainvoke({"messages": [HumanMessage(content="work")]})
    assert any(isinstance(m, ToolMessage) and m.content == "helper" for m in result["messages"])


async def test_actual_sdk_task_inflight_survives_delete_refresh():
    documents = {"user/helper/SYSTEM.md": manifest("helper")}
    started, proceed = asyncio.Event(), asyncio.Event()

    def proxy(source):
        async def run(state):
            started.set()
            await proceed.wait()
            return {"messages": [AIMessage(content="active result")]}

        return {
            "name": source.name,
            "description": source.manifest.description,
            "runnable": RunnableLambda(run),
        }

    registry = registry_for(documents, proxy)
    await registry.refresh()
    middleware = LiveSubagentMiddleware(registry, backend=None)
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "work", "subagent_type": "helper"},
                        "id": "active",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = create_deep_agent(model=model, middleware=[middleware])
    active = asyncio.create_task(graph.ainvoke({"messages": [HumanMessage(content="work")]}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        documents.clear()
        await registry.refresh()
        assert not registry.callables
        proceed.set()
        result = await asyncio.wait_for(active, 2)
        assert any(
            isinstance(m, ToolMessage) and m.content == "active result" for m in result["messages"]
        )
    finally:
        proceed.set()
        if not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)


async def test_refresh_preserves_inflight_runnable_and_publishes_atomically():
    documents = {"user/helper/SYSTEM.md": manifest("helper")}
    started, proceed = asyncio.Event(), asyncio.Event()

    def proxy(source):
        async def run(state):
            started.set()
            await proceed.wait()
            return {"messages": [AIMessage(content=source.name)]}

        return {
            "name": source.name,
            "description": source.manifest.description,
            "runnable": RunnableLambda(run),
        }

    registry = registry_for(documents, proxy)
    await registry.refresh()
    snapshot = LiveSubagentMiddleware(registry, backend=None)._snapshot()
    active = asyncio.create_task(registry.callables["helper"].ainvoke({"messages": []}))
    await started.wait()
    documents.clear()
    await registry.refresh()
    assert not registry.callables
    assert "helper" in snapshot.tools[0].description
    proceed.set()
    assert (await active)["messages"][0].content == "helper"

    documents["user/new/SYSTEM.md"] = manifest("new")
    registry._proxy_factory = lambda source: (_ for _ in ()).throw(RuntimeError("build failed"))
    with pytest.raises(RuntimeError, match="build failed"):
        await registry.refresh()
    assert registry.descriptors == ()


async def test_reserved_names_and_invalid_manifests_are_skipped():
    registry = registry_for(
        {"user/reserved/SYSTEM.md": manifest("reserved"), "user/bad/SYSTEM.md": "invalid"},
        reserved_names={"reserved"},
    )
    await registry.refresh()
    assert registry.callables == {}


async def test_compiled_routine_retains_initially_empty_live_map():
    documents = {}
    registry = registry_for(documents)
    routine = validate_routine_graph_payload(
        {
            "id": "live-test",
            "name": "Live test",
            "description": "Live delegation",
            "version": "2.0.0",
            "entry": "delegate",
            "nodes": [
                {
                    "id": "delegate",
                    "kind": "agent",
                    "output_key": "result",
                    "config": {"agent_name": "helper", "prompt_template": "help"},
                }
            ],
            "edges": [],
        }
    )
    graph = compile_routine_graph(
        routine,
        model=Model(responses=[AIMessage(content="unused")]),
        agent_registry=registry.callables,
    )
    documents["user/helper/SYSTEM.md"] = manifest("helper")
    await registry.refresh()
    result = await graph.ainvoke({"messages": [HumanMessage(content="run")]})
    assert result["data"]["result"] == "helper"
    documents.clear()
    await registry.refresh()
    with pytest.raises(KeyError, match="unknown agent 'helper'"):
        await graph.ainvoke({"messages": [HumanMessage(content="run again")]})


@pytest.mark.parametrize("create_graph", [create_agent, create_deep_agent])
async def test_javascript_dispatches_agents_added_and_replaced_after_compile(create_graph):
    """Interpreter dispatch resolves live targets, with or without a static task."""
    documents = {}

    def proxy(source):
        power = 2 if source.name == "square" else 3

        def calculate(state):
            values = json.loads(state["messages"][-1].content)
            return {"messages": [AIMessage(content=str(sum(value**power for value in values)))]}

        return {
            "name": source.name,
            "description": source.manifest.description,
            "runnable": RunnableLambda(calculate),
        }

    registry = registry_for(documents, proxy)
    model = Model(responses=[AIMessage(content="initial")])
    graph = create_graph(
        model=model,
        middleware=[
            _create_javascript_middleware(),
            LiveSubagentMiddleware(registry, backend=None),
        ],
    )

    async def delegate(name):
        model.i = 0
        model.responses = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "javascript",
                        "args": {
                            "code": f'Number(await task({{description: "[2,3]", subagentType: "{name}"}}))'
                        },
                        "id": f"javascript-{name}",
                    }
                ],
            ),
            AIMessage(content="complete"),
        ]
        result = await graph.ainvoke({"messages": [HumanMessage(content="calculate")]})
        return [m.content for m in result["messages"] if isinstance(m, ToolMessage)]

    documents["user/square/SYSTEM.md"] = manifest("square")
    assert await delegate("square") == ["<result>13</result>"]
    documents.clear()
    documents["user/cube/SYSTEM.md"] = manifest("cube")
    assert await delegate("cube") == ["<result>35</result>"]
