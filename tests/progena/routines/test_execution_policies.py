"""Policies govern real LangGraph execution and keep each run's settings stable."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import NodeTimeoutError

from src.progena.agent.filesystem.policies import PolicyDirectoryBackend
from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import validate_routine_graph_payload
from src.progena.routines.revisions import NullRoutineRevisionReader
from src.progena.routines.subagent import create_routine_executor, resume_routine_thread
from src.progena.settings.policies import ExecutionPolicy, PolicyValues, validate_policy


def graph(nodes, edges=None):
    return {
        "id": "policy-test",
        "name": "Policy test",
        "description": "",
        "version": "2.0.0",
        "entry": nodes[0]["id"],
        "nodes": nodes,
        "edges": edges or [],
        "policy_id": "test",
    }


def prompt(config=None):
    return {
        "id": "call",
        "kind": "prompt",
        "output_key": "call",
        "config": config or {"prompt_template": "hello"},
    }


class Model:
    def __init__(self, fail=0, delay=0):
        self.calls = 0
        self.fail = fail
        self.delay = delay

    async def ainvoke(self, messages):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.calls <= self.fail:
            raise ValueError("invalid model response")
        return AIMessage(content="hello")


@pytest.mark.parametrize("ttl,calls", [(None, 1), (60, 1), (0, 2)])
async def test_native_cache_reuses_identical_inputs_across_runs_and_respects_ttl(ttl, calls):
    model = Model()
    compiled = compile_routine_graph(
        validate_routine_graph_payload(graph([prompt()])),
        model=model,
        execution_policy=PolicyValues(cache_enabled=True, cache_ttl=ttl),
    )
    await compiled.ainvoke({"data": {}})
    await compiled.ainvoke({"data": {}})
    assert model.calls == calls


async def test_node_can_disable_policy_cache():
    model = Model()
    compiled = compile_routine_graph(
        validate_routine_graph_payload(
            graph([prompt({"prompt_template": "hello", "cache": False})])
        ),
        model=model,
        execution_policy=PolicyValues(cache_enabled=True),
    )
    await compiled.ainvoke({"data": {}})
    await compiled.ainvoke({"data": {}})
    assert model.calls == 2


async def test_policy_retry_category_and_node_override():
    policy = PolicyValues(
        retry={"max_retries": 2, "initial_interval": 0, "retry_on": "model_response"}
    )
    model = Model(fail=2)
    compiled = compile_routine_graph(
        validate_routine_graph_payload(graph([prompt()])), model=model, execution_policy=policy
    )
    await compiled.ainvoke({"data": {}})
    assert model.calls == 3
    model = Model(fail=2)
    compiled = compile_routine_graph(
        validate_routine_graph_payload(
            graph([prompt({"prompt_template": "hello", "retry": {"max_retries": 0}})])
        ),
        model=model,
        execution_policy=policy,
    )
    with pytest.raises(ValueError):
        await compiled.ainvoke({"data": {}})
    assert model.calls == 1


async def test_idle_timeout_uses_native_watchdog():
    compiled = compile_routine_graph(
        validate_routine_graph_payload(graph([prompt()])),
        model=Model(delay=0.1),
        execution_policy=PolicyValues(idle_timeout=0.01),
    )
    with pytest.raises(NodeTimeoutError) as caught:
        await compiled.ainvoke({"data": {}})
    assert caught.value.kind == "idle"


async def test_policy_default_profile_and_node_override():
    resolver = AsyncMock(return_value=Model())
    for config, expected in [
        ({"prompt_template": "hello"}, "policy-model"),
        ({"prompt_template": "hello", "model_profile": "node-model"}, "node-model"),
    ]:
        compiled = compile_routine_graph(
            validate_routine_graph_payload(graph([prompt(config)])),
            model=Model(),
            model_profile_resolver=resolver,
            execution_policy=PolicyValues(default_model_profile="policy-model"),
        )
        await compiled.ainvoke({"data": {}})
        assert resolver.call_args.args == (expected,)


@pytest.mark.parametrize("node_limit,expected", [(None, 2), (1, 1), (3, 3)])
async def test_policy_fan_out_default_is_overridden_on_the_node(node_limit, expected):
    config = {"mode": "send", "items_from": "items", "item_key": "item"}
    if node_limit:
        config["max_items"] = node_limit
    nodes = [
        {"id": "fan", "kind": "foreach", "output_key": "fan", "config": config},
        {"id": "body", "kind": "eval", "output_key": "body", "config": {"expression": "item"}},
    ]
    compiled = compile_routine_graph(
        validate_routine_graph_payload(
            graph(nodes, [{"from": "fan", "to": "body", "level": "normal"}])
        ),
        model=Model(),
        execution_policy=PolicyValues(default_fan_out_limit=2),
    )
    result = await compiled.ainvoke({"data": {"items": [1, 2, 3]}})
    assert len(result["run_meta"]["body"]) == expected


async def test_virtual_policy_files_validate_read_write_and_edit():
    documents = {}

    async def save(key, doc):
        documents[key] = doc

    sm = SimpleNamespace(
        get_execution_policy=AsyncMock(side_effect=lambda key: documents.get(key)),
        save_execution_policy=save,
        list_execution_policies=AsyncMock(side_effect=lambda: list(documents.values())),
    )
    backend = PolicyDirectoryBackend(sm)
    policy = ExecutionPolicy(id="test", name="Test").model_dump(mode="json")
    assert (await backend.awrite("/test.json", json.dumps(policy))).error is None
    assert (await backend.awrite("/other.json", json.dumps(policy))).error
    policy["values"]["concurrency_limit"] = 0
    assert (await backend.awrite("/test.json", json.dumps(policy))).error
    assert (
        await backend.aedit("/test.json", '"concurrency_limit": 4', '"concurrency_limit": 2')
    ).error is None
    read = await backend.aread("/test.json")
    assert json.loads(read.file_data["content"])["values"]["concurrency_limit"] == 2
    assert len((await backend.als("/")).entries) == 1


async def test_paused_run_keeps_policy_snapshot_even_if_policy_is_deleted():
    doc = graph(
        [
            {
                "id": "approval",
                "kind": "human",
                "output_key": "approval",
                "config": {"message": "Approve?"},
            },
            {"id": "after", "kind": "eval", "output_key": "after", "config": {"expression": "42"}},
        ],
        [{"from": "approval", "to": "after", "level": "normal"}],
    )
    policy = ExecutionPolicy(id="test", name="Test").model_dump(mode="json")
    sm = SimpleNamespace(
        get_model_profiles_config=AsyncMock(return_value={}),
        get_routine_graph=AsyncMock(return_value=doc),
        get_execution_policy=AsyncMock(return_value=policy),
    )
    saver = MemorySaver()
    run = create_routine_executor(
        routine_id=doc["id"],
        model=Model(),
        state_manager=sm,
        revision_reader=NullRoutineRevisionReader(),
        agent_registry={},
        routine_registry={},
        checkpointer=saver,
    )
    paused = await run.ainvoke({})
    sm.get_execution_policy.return_value = None
    result = await resume_routine_thread(
        thread_id=paused["_routine_thread_id"],
        answer="approve",
        model=Model(),
        state_manager=sm,
        revision_reader=NullRoutineRevisionReader(),
        agent_registry={},
        routine_registry={},
        checkpointer=saver,
    )
    assert result["status"] == "completed"
    snapshot = await saver.aget_tuple({"configurable": {"thread_id": paused["_routine_thread_id"]}})
    assert snapshot.checkpoint["channel_values"]["data"]["after"] == 42
    assert sm.get_execution_policy.await_count == 1


@pytest.mark.parametrize(
    "values",
    [{"cache_ttl": -1}, {"concurrency_limit": 0}, {"cache_enabled": "yes"}, {"unexpected": True}],
)
def test_invalid_values_are_rejected(values):
    with pytest.raises(ValueError):
        validate_policy({"id": "test", "name": "Test", "values": values})


@pytest.mark.parametrize("field", ["input_schema", "output_schema", "node_output"])
def test_execution_policy_metadata_cannot_be_overwritten_by_graph_fields(field):
    document = graph([prompt()])
    if field == "node_output":
        document["nodes"][0]["output_key"] = "_execution_policy"
    else:
        document[field] = {"type": "object", "properties": {"_execution_policy": {}}}
    with pytest.raises(ValueError, match="reserved execution metadata"):
        validate_routine_graph_payload(document)


@pytest.mark.parametrize("limit", [1, 2])
async def test_policy_concurrency_limits_actual_parallel_send_workers(limit):
    class ConcurrentModel(Model):
        active = 0
        peak = 0

        async def ainvoke(self, messages):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.03)
                return AIMessage(content="done")
            finally:
                self.active -= 1

    model = ConcurrentModel()
    nodes = [
        {
            "id": "fan",
            "kind": "foreach",
            "output_key": "fan",
            "config": {"mode": "send", "items_from": "items", "item_key": "item"},
        },
        {
            "id": "worker",
            "kind": "prompt",
            "output_key": "worker",
            "config": {"prompt_template": "Process {item}"},
        },
    ]
    compiled = compile_routine_graph(
        validate_routine_graph_payload(
            graph(nodes, [{"from": "fan", "to": "worker", "level": "normal"}])
        ),
        model=model,
        execution_policy=PolicyValues(concurrency_limit=limit),
    )
    await compiled.ainvoke({"data": {"items": [1, 2, 3]}})
    assert model.peak == limit


@pytest.mark.parametrize("node_cache", [True, {}])
async def test_node_cache_enable_inherits_policy_ttl(node_cache):
    model = Model()
    compiled = compile_routine_graph(
        validate_routine_graph_payload(
            graph([prompt({"prompt_template": "hello", "cache": node_cache})])
        ),
        model=model,
        execution_policy=PolicyValues(cache_enabled=False, cache_ttl=0),
    )
    await compiled.ainvoke({"data": {}})
    await compiled.ainvoke({"data": {}})
    assert model.calls == 2
