"""Tests for compiling and running a RoutineGraph (app.progenitor.routines.factory)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.progenitor.routines.factory import (
    compile_routine_graph,
    extract_json_object,
    normalize_message_content,
    resolve_data_path,
)
from app.progenitor.routines.graph import validate_routine_graph_payload

_EXAMPLE_PATH = Path(__file__).parent / "fixtures" / "repo_setup_worker.json"


@pytest.fixture
def example_payload() -> dict:
    return json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def example_graph(example_payload):
    return validate_routine_graph_payload(example_payload)


class FakeModel:
    """Returns queued responses in order, one per ``.ainvoke`` call."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self._responses.pop(0))


class FakeAgent:
    """Stands in for the ``agent_registry["repo-manager"]`` compiled target."""

    def __init__(self, response: str):
        self._response = response
        self.calls: list[dict] = []

    async def ainvoke(self, state):
        self.calls.append(state)
        return {"messages": [AIMessage(content=self._response)]}


def _parse_response(org: str = "acme-oss", override_dirty: bool = False) -> str:
    return json.dumps(
        {"host": "github.com", "org": org, "repo": f"{org}/foo", "override_dirty": override_dirty}
    )


def _assess_response(**overrides) -> str:
    base = {"accessible": True, "cloned": True, "dirty": False, "behind": False}
    base.update(overrides)
    return json.dumps(base)


async def _run(graph, model, agent_registry, request="please set up acme-oss/foo"):
    runnable = compile_routine_graph(graph, model=model, agent_registry=agent_registry)
    return await runnable.ainvoke({"messages": [HumanMessage(content=request)]})


class TestRepoSetupWorkerRouting:
    async def test_invalid_domain_takes_router_default_branch(self, example_graph):
        model = FakeModel([_parse_response(org="not-allowed"), "Sorry, that org isn't supported."])
        result = await _run(example_graph, model, agent_registry={})
        assert result["data"]["domain_check"] is False
        assert "respond_invalid_domain" in result["data"]
        assert "assess" not in result["data"]

    async def test_up_to_date_reaches_terminal_response(self, example_graph):
        model = FakeModel([_parse_response(), "All set, already up to date."])
        agent = FakeAgent(_assess_response())
        result = await _run(example_graph, model, agent_registry={"repo-manager": agent})
        assert result["data"]["route_decision"] == "up_to_date"
        assert result["messages"][-1].content == "All set, already up to date."

    async def test_dirty_without_override_routes_to_dirty(self, example_graph):
        model = FakeModel([_parse_response(), "Local changes found."])
        agent = FakeAgent(_assess_response(dirty=True))
        result = await _run(example_graph, model, agent_registry={"repo-manager": agent})
        assert result["data"]["route_decision"] == "dirty"

    async def test_dirty_with_override_routes_to_dirty_override(self, example_graph):
        model = FakeModel([_parse_response(override_dirty=True), "Continuing anyway."])
        agent = FakeAgent(_assess_response(dirty=True))
        result = await _run(example_graph, model, agent_registry={"repo-manager": agent})
        assert result["data"]["route_decision"] == "dirty_override"

    async def test_not_cloned_routes_to_clone_then_respond(self, example_graph):
        model = FakeModel([_parse_response(), "Cloned successfully."])
        agent = FakeAgent(_assess_response(cloned=False, dirty=False, behind=False))
        result = await _run(example_graph, model, agent_registry={"repo-manager": agent})
        assert result["data"]["route_decision"] == "not_cloned"
        assert "respond_cloned" in result["data"]
        # clone + respond_cloned each call the agent/model once beyond parse.
        assert len(agent.calls) == 2  # assess, then clone

    async def test_behind_routes_to_pull_then_respond(self, example_graph):
        model = FakeModel([_parse_response(), "Pulled latest."])
        agent = FakeAgent(_assess_response(behind=True))
        result = await _run(example_graph, model, agent_registry={"repo-manager": agent})
        assert result["data"]["route_decision"] == "behind"
        assert "respond_pulled" in result["data"]

    async def test_no_access_routes_to_respond_no_access(self, example_graph):
        model = FakeModel([_parse_response(), "Could not verify access."])
        agent = FakeAgent(_assess_response(accessible=False))
        result = await _run(example_graph, model, agent_registry={"repo-manager": agent})
        assert result["data"]["route_decision"] == "no_access"

    async def test_missing_agent_registry_entry_raises_keyerror(self, example_graph):
        model = FakeModel([_parse_response()])
        with pytest.raises(KeyError, match="unknown agent 'repo-manager'"):
            await _run(example_graph, model, agent_registry={})

    async def test_data_accumulates_without_clobbering_prior_keys(self, example_graph):
        model = FakeModel([_parse_response(), "done"])
        agent = FakeAgent(_assess_response())
        result = await _run(example_graph, model, agent_registry={"repo-manager": agent})
        # Every node along the up_to_date path left its output in `data`.
        for key in ("request", "parse", "parse_json", "domain_check", "assess", "assess_json", "route_decision"):
            assert key in result["data"], key


class TestSubagentRegistry:
    async def test_missing_subagent_raises_keyerror(self):
        payload = {
            "id": "g",
            "name": "g",
            "description": "",
            "version": "1.0.0",
            "entry": "delegate",
            "nodes": [
                {
                    "id": "delegate",
                    "kind": "subagent",
                    "output_key": "delegate",
                    "config": {"subagent_name": "helper", "prompt_template": "{request}"},
                }
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        model = FakeModel([])
        with pytest.raises(KeyError, match="unknown subagent 'helper'"):
            await _run(graph, model, agent_registry={})

    async def test_subagent_result_is_extracted_and_stored(self):
        payload = {
            "id": "g",
            "name": "g",
            "description": "",
            "version": "1.0.0",
            "entry": "delegate",
            "nodes": [
                {
                    "id": "delegate",
                    "kind": "subagent",
                    "output_key": "delegate",
                    "config": {"subagent_name": "helper", "prompt_template": "{request}"},
                }
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)

        class FakeSubgraph:
            async def ainvoke(self, state):
                return {"messages": [*state["messages"], AIMessage(content="delegated result")]}

        model = FakeModel([])
        runnable = compile_routine_graph(graph, model=model, subagent_registry={"helper": FakeSubgraph()})
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result["data"]["delegate"] == "delegated result"
        assert result["messages"][-1].content == "delegated result"


class TestCompileTimeErrors:
    def test_unknown_entry_raises_valueerror(self, example_graph):
        # Pydantic doesn't validate on plain attribute assignment (validate_assignment
        # is off by default), so this reaches compile_routine_graph's own entry check.
        example_graph.entry = "does-not-exist"
        with pytest.raises(ValueError, match="entry references unknown node"):
            compile_routine_graph(example_graph, model=FakeModel([]))


class TestHelpers:
    def test_normalize_message_content_string(self):
        assert normalize_message_content("hi") == "hi"

    def test_normalize_message_content_block_list(self):
        content = [{"type": "text", "text": "a"}, {"type": "image"}, {"type": "text", "text": "b"}]
        assert normalize_message_content(content) == "ab"

    def test_normalize_message_content_fallback(self):
        assert normalize_message_content(42) == "42"

    def test_extract_json_object_plain(self):
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_extract_json_object_fenced(self):
        text = 'Sure, here:\n```json\n{"a": 1}\n```\nThanks'
        assert extract_json_object(text) == {"a": 1}

    def test_extract_json_object_balanced_substring(self):
        text = 'prefix noise {"a": {"b": 1}} trailing noise'
        assert extract_json_object(text) == {"a": {"b": 1}}

    def test_extract_json_object_invalid_returns_none(self):
        assert extract_json_object("not json at all") is None

    def test_resolve_data_path_nested(self):
        assert resolve_data_path({"a": {"b": 2}}, "a.b") == 2

    def test_resolve_data_path_missing_raises_keyerror(self):
        with pytest.raises(KeyError, match="missing state path: a.b"):
            resolve_data_path({"a": {}}, "a.b")
