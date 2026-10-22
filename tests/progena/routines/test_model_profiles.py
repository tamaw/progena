"""Named profile routing across direct model calls and delegated agents."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.progena.llm.provider_context as provider_runtime
from src.progena.agent.model_profiles import resolve_model_profile
from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import validate_routine_graph_payload
from tests.progena.routines.test_factory import FakeModel, FakeStructuredModel


class _ProviderRepository:
    def resolve(self, provider_id=None, model_override=None):
        return {
            "provider_id": provider_id or "foundry-1",
            "provider_kind": "foundry",
            "base_url": "https://example.openai.azure.com/openai/v1/responses",
            "api_key": "test-key",
            "model_default": model_override or "default",
            "revision": 1,
        }


class _ConfigurableFakeModel(FakeModel):
    async def ainvoke(self, messages, config=None, **kwargs):
        return await super().ainvoke(messages)


class _ConfigurableFakeStructuredModel(FakeStructuredModel):
    def with_structured_output(self, *args, **kwargs):
        target = super().with_structured_output(*args, **kwargs)

        class _ConfigurableStructuredRunnable:
            async def ainvoke(self, messages, config=None, **invoke_kwargs):
                return await target.ainvoke(messages)

        return _ConfigurableStructuredRunnable()


def graph_for(kind, profile="selected", execution="wait"):
    config = {"model_profile": profile}
    if kind == "prompt":
        config["prompt_template"] = "Say hello"
    elif kind == "classify":
        config.update(
            rubric_prompt="Say hello",
            response_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        )
    else:
        config.update(agent_name="greeter", prompt_template="Say hello", execution=execution)
    return validate_routine_graph_payload(
        {
            "id": "profiles",
            "name": "Profiles",
            "description": "",
            "version": "2.0.0",
            "entry": "call",
            "nodes": [{"id": "call", "kind": kind, "output_key": "call", "config": config}],
            "edges": [],
        }
    )


@pytest.mark.parametrize("kind", ["prompt", "classify"])
async def test_named_profile_is_live_even_when_compiled_graph_is_reused(kind, monkeypatch):
    profile = {
        "selected": {"provider_id": "foundry-1", "deployment": "first", "reasoning_enabled": False}
    }
    state = SimpleNamespace(get_model_profiles_config=AsyncMock(side_effect=lambda: profile))
    deployments = []

    def create(config, *, profile_name=None):
        deployments.append(config.model_default)
        assert config.reasoning_enabled is False
        return (
            _ConfigurableFakeStructuredModel([{"answer": config.model_default}])
            if kind == "classify"
            else _ConfigurableFakeModel([config.model_default])
        )

    monkeypatch.setattr("src.progena.agent.adapters.create_langchain_model", create)
    monkeypatch.setattr(provider_runtime, "get_provider_repository", lambda: _ProviderRepository())
    parent = FakeModel([])
    compiled = compile_routine_graph(
        graph_for(kind),
        model=parent,
        model_profile_resolver=lambda name: resolve_model_profile(state, name),
    )
    for expected in ["first", "second"]:
        profile["selected"]["deployment"] = expected
        result = await compiled.ainvoke({"data": {}})
        assert result["data"]["call"] == ({"answer": expected} if kind == "classify" else expected)
    assert deployments == ["first", "second"]
    profile.clear()
    with pytest.raises(ValueError, match="Unknown model profile"):
        await compiled.ainvoke({"data": {}})


@pytest.mark.parametrize("execution", ["wait", "background"])
async def test_agent_node_propagates_profile_without_changing_agent_definition(execution):
    seen = []

    async def target(state, config):
        seen.append(config["configurable"]["model_profile"])
        return {"messages": [AIMessage(content="hello")]}

    async def launch(name, prompt, **kwargs):
        seen.append(kwargs["model_profile"])
        return {"task_id": "task", "status": "running"}

    compiled = compile_routine_graph(
        graph_for("agent", execution=execution),
        model=FakeModel([]),
        agent_registry={"greeter": RunnableLambda(target)},
        agent_background_runner=launch,
    )
    result = await compiled.ainvoke({"data": {}})
    assert seen == ["selected"]
    assert result["data"]["call"] == (
        "hello" if execution == "wait" else {"task_id": "task", "status": "running"}
    )


@pytest.mark.parametrize("kind", ["eval", "a2a", "foreach"])
def test_other_node_kinds_reject_model_profiles(kind):
    configs = {
        "eval": {"expression": "1"},
        "a2a": {"agent_name": "remote", "prompt_template": "hello"},
        "foreach": {"mode": "send", "items_from": "items", "item_key": "item"},
    }
    with pytest.raises(ValueError, match="schema validation"):
        validate_routine_graph_payload(
            {
                "id": "invalid",
                "name": "Invalid",
                "description": "",
                "version": "2.0.0",
                "entry": "call",
                "nodes": [
                    {
                        "id": "call",
                        "kind": kind,
                        "output_key": "call",
                        "config": {**configs[kind], "model_profile": "selected"},
                    }
                ],
                "edges": [],
            }
        )
