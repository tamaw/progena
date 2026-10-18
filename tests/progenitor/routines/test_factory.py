"""Tests for compiling and running a RoutineGraph (app.progenitor.routines.factory)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from app.progenitor.policies import PolicyValues
from app.progenitor.routines.factory import (
    compile_routine_graph,
    extract_json_object,
    normalize_message_content,
    resolve_data_path,
)
from app.progenitor.routines.graph import validate_routine_graph_payload

_EXAMPLE_PATH = Path(__file__).parent / "fixtures" / "reference_routine.json"


@pytest.fixture
def example_payload() -> dict:
    return json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def example_graph(example_payload):
    return validate_routine_graph_payload(example_payload)


class FakeModel:
    """Returns queued responses in order, one per ``.ainvoke`` call.

    ``usage`` is an optional list of ``UsageMetadata``-shaped dicts, one per
    queued response -- omit to leave every response's ``usage_metadata`` at
    its default ``None`` (the "no reported usage" case)."""

    def __init__(self, responses: list[str], usage: list[dict] | None = None):
        self._responses = list(responses)
        self._usage = list(usage) if usage is not None else None
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        content = self._responses.pop(0)
        usage_metadata = self._usage.pop(0) if self._usage is not None else None
        return AIMessage(content=content, usage_metadata=usage_metadata)


class FakeStructuredModel:
    """Stands in for a BaseChatModel whose ``with_structured_output(...)`` is
    exercised (method='json_schema', strict=True, include_raw=True) -- returns
    queued parsed values in order, one per bound Runnable's ``.ainvoke`` call.
    A queued ``None`` simulates a schema-validation failure. ``usage`` mirrors
    FakeModel's: an optional list of UsageMetadata dicts, one per response."""

    def __init__(self, parsed_responses: list[dict | None], usage: list[dict] | None = None):
        self._responses = list(parsed_responses)
        self._usage = list(usage) if usage is not None else None
        self.calls: list[list] = []
        self.bind_calls: list[dict] = []

    async def ainvoke(self, messages):
        raise AssertionError("FakeStructuredModel.ainvoke() called without with_structured_output")

    def with_structured_output(self, schema, *, method, strict, include_raw=False):
        self.bind_calls.append(
            {"schema": schema, "method": method, "strict": strict, "include_raw": include_raw}
        )
        outer = self

        class _StructuredRunnable:
            async def ainvoke(_self, messages):
                outer.calls.append(messages)
                parsed = outer._responses.pop(0)
                usage_metadata = outer._usage.pop(0) if outer._usage is not None else None
                raw = AIMessage(
                    content=json.dumps(parsed) if parsed is not None else "",
                    usage_metadata=usage_metadata,
                )
                error = None if parsed is not None else ValueError("simulated schema violation")
                return {"raw": raw, "parsed": parsed, "parsing_error": error}

        return _StructuredRunnable()


class FakeAgent:
    """Stands in for the ``agent_registry["repo-manager"]`` compiled target."""

    def __init__(self, response: str, usage: dict | None = None):
        self._response = response
        self._usage = usage
        self.calls: list[dict] = []

    async def ainvoke(self, state, **kwargs):
        self.calls.append(state)
        return {"messages": [AIMessage(content=self._response, usage_metadata=self._usage)]}


def _parse_response(
    org: str = "example-org", override_dirty: bool = False, host: str = "github.com"
) -> str:
    return json.dumps(
        {"host": host, "org": org, "repo": f"{org}/foo", "override_dirty": override_dirty}
    )


def _assess_response(**overrides) -> str:
    base = {"accessible": True, "cloned": True, "dirty": False, "behind": False}
    base.update(overrides)
    return json.dumps(base)


async def _run(graph, model, agent_registry, request="please set up example-org/foo"):
    runnable = compile_routine_graph(graph, model=model, agent_registry=agent_registry)
    return await runnable.ainvoke({"messages": [HumanMessage(content=request)]})


class TestRepoSetupWorkerRouting:
    async def test_invalid_domain_takes_router_default_branch(self, example_graph):
        # The gate is the host alone -- access to a given org is GitHub's call,
        # reported downstream by `assess` as `accessible`.
        model = FakeModel([_parse_response(host="gitlab.com"), "Sorry, that host isn't supported."])
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
        for key in (
            "request",
            "parse",
            "parse_json",
            "domain_check",
            "assess",
            "assess_json",
            "route_decision",
        ):
            assert key in result["data"], key


class TestAgentResponseSchema:
    @staticmethod
    def _graph(response_schema: dict | None = None):
        config = {"agent_name": "repo-manager", "prompt_template": "{request}"}
        if response_schema is not None:
            config["response_schema"] = response_schema
        payload = {
            "id": "agent-output",
            "name": "Agent output",
            "description": "",
            "version": "2.0.0",
            "entry": "call",
            "nodes": [{"id": "call", "kind": "agent", "output_key": "result", "config": config}],
            "edges": [],
        }
        if response_schema is not None:
            payload["$defs"] = {
                "repo_result": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["action", "clean"],
                    "properties": {
                        "action": {"type": "string"},
                        "clean": {"type": "boolean"},
                    },
                }
            }
        return validate_routine_graph_payload(payload)

    async def test_agent_without_response_schema_preserves_text_output(self):
        graph = self._graph()
        result = await _run(
            graph,
            FakeModel([]),
            agent_registry={"repo-manager": FakeAgent("Repository is ready.")},
        )

        assert result["data"]["result"] == "Repository is ready."
        assert "result_json" not in result["data"]

    async def test_agent_response_schema_stores_validated_object(self):
        graph = self._graph({"$ref": "#/$defs/repo_result"})
        result = await _run(
            graph,
            FakeModel([]),
            agent_registry={
                "repo-manager": FakeAgent(json.dumps({"action": "pull", "clean": True}))
            },
        )

        assert result["data"]["result"] == {"action": "pull", "clean": True}
        assert "result_json" not in result["data"]

    async def test_agent_response_schema_rejects_invalid_result(self):
        graph = self._graph({"$ref": "#/$defs/repo_result"})

        with pytest.raises(ValueError, match="structured response failed schema validation"):
            await _run(
                graph,
                FakeModel([]),
                agent_registry={"repo-manager": FakeAgent(json.dumps({"action": "pull"}))},
            )


class TestAgentInstruction:
    @staticmethod
    def _graph(instruction: str = "workflows/greet.md"):
        return validate_routine_graph_payload(
            {
                "id": "agent-instruction",
                "name": "Agent instruction",
                "description": "",
                "version": "2.0.0",
                "entry": "call",
                "nodes": [
                    {
                        "id": "call",
                        "kind": "agent",
                        "output_key": "result",
                        "config": {"agent_name": "greeter", "instruction": instruction},
                    }
                ],
                "edges": [],
            }
        )

    async def test_renders_instruction_with_routine_state(self):
        agent = FakeAgent("Hello")
        graph = self._graph()
        runnable = compile_routine_graph(
            graph,
            model=FakeModel([]),
            agent_registry={"greeter": agent},
            instruction_loader=lambda path: (
                "---\ntitle: Greet\ninputs: {name: string}\n---\nHello {name}!"
            ),
        )

        result = await runnable.ainvoke({"data": {"name": "Ada"}})

        assert result["data"]["result"] == "Hello"
        assert agent.calls[0]["messages"][0].content == "Hello Ada!"

    def test_missing_instruction_fails_clearly(self):
        graph = self._graph()

        with pytest.raises(ValueError, match="instruction 'workflows/greet.md' cannot be loaded"):
            compile_routine_graph(
                graph, model=FakeModel([]), agent_registry={"greeter": FakeAgent("x")}
            )

    async def test_unknown_instruction_fails_clearly(self):
        graph = self._graph()
        runnable = compile_routine_graph(
            graph,
            model=FakeModel([]),
            agent_registry={"greeter": FakeAgent("x")},
            instruction_loader=lambda path: None,
        )

        with pytest.raises(ValueError, match="instruction 'workflows/greet.md' not found"):
            await runnable.ainvoke({"data": {}})


class TestRoutineRegistry:
    async def test_missing_routine_raises_keyerror(self):
        payload = {
            "id": "g",
            "name": "g",
            "description": "",
            "version": "2.0.0",
            "entry": "delegate",
            "nodes": [
                {
                    "id": "delegate",
                    "kind": "routine",
                    "output_key": "delegate",
                    "config": {"routine_name": "helper"},
                }
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        model = FakeModel([])
        with pytest.raises(KeyError, match="unknown routine 'helper'"):
            await _run(graph, model, agent_registry={})

    async def test_routine_result_is_extracted_and_stored(self):
        payload = {
            "id": "g",
            "name": "g",
            "description": "",
            "version": "2.0.0",
            "entry": "delegate",
            "nodes": [
                {
                    "id": "delegate",
                    "kind": "routine",
                    "output_key": "delegate",
                    "config": {"routine_name": "helper"},
                }
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)

        class FakeSubgraph:
            async def ainvoke(self, state, **kwargs):
                return {"messages": [*state["messages"], AIMessage(content="delegated result")]}

        model = FakeModel([])
        runnable = compile_routine_graph(
            graph, model=model, routine_registry={"helper": FakeSubgraph()}
        )
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


class FlakyModel:
    """Raises for the first ``fail_times`` calls, then succeeds -- exercises RetryPolicy."""

    def __init__(self, fail_times: int, response: str):
        self.fail_times = fail_times
        self.response = response
        self.attempts = 0

    async def ainvoke(self, messages):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("simulated transient failure")
        return AIMessage(content=self.response)


def _single_prompt_step_payload(
    settings: dict | None = None, node_retry: dict | None = None
) -> dict:
    config = {"prompt_template": "go"}
    if node_retry is not None:
        config["retry"] = {"retry_on": "all", **node_retry}
    return {
        "id": "flaky-step",
        "name": "flaky-step",
        "description": "",
        "version": "2.0.0",
        "entry": "step",
        "nodes": [{"id": "step", "kind": "prompt", "output_key": "step", "config": config}],
        "edges": [],
    }


class TestGraphWideRetryDefault:
    """settings.retry applies via StateGraph.set_node_defaults to nodes without their own config.retry."""

    async def test_settings_retry_covers_a_node_with_no_retry_of_its_own(self):
        payload = _single_prompt_step_payload()
        graph = validate_routine_graph_payload(payload)
        model = FlakyModel(fail_times=2, response="ok")
        runnable = compile_routine_graph(
            graph,
            model=model,
            execution_policy=PolicyValues(
                retry={"max_retries": 2, "initial_interval": 0, "retry_on": "all"}
            ),
        )
        result = await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})
        assert result["data"]["step"] == "ok"
        assert model.attempts == 3

    async def test_without_settings_retry_the_first_failure_propagates(self):
        payload = _single_prompt_step_payload()
        graph = validate_routine_graph_payload(payload)
        model = FlakyModel(fail_times=2, response="ok")
        runnable = compile_routine_graph(graph, model=model)
        with pytest.raises(RuntimeError, match="simulated transient failure"):
            await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})
        assert model.attempts == 1

    async def test_node_level_retry_overrides_the_graph_default(self):
        # Graph default allows 3 attempts, but this node opts into only 1 -- its
        # own config.retry must win per StateGraph.set_node_defaults semantics.
        payload = _single_prompt_step_payload(
            node_retry={"max_retries": 0, "initial_interval": 0},
        )
        graph = validate_routine_graph_payload(payload)
        model = FlakyModel(fail_times=2, response="ok")
        runnable = compile_routine_graph(
            graph,
            model=model,
            execution_policy=PolicyValues(retry={"max_retries": 2, "retry_on": "all"}),
        )
        with pytest.raises(RuntimeError, match="simulated transient failure"):
            await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})
        assert model.attempts == 1


class TestRetryPolicyContract:
    """max_retries counts retries, not attempts."""

    async def test_max_retries_zero_runs_exactly_once(self):
        """max_retries: 0 compiles to max_attempts == 1, i.e. one attempt, zero retries."""
        payload = _single_prompt_step_payload(node_retry={"max_retries": 0, "initial_interval": 0})
        graph = validate_routine_graph_payload(payload)
        compiled = compile_routine_graph(graph, model=FakeModel([]))
        assert compiled.nodes["step"].retry_policy[0].max_attempts == 1
        # Observable check: the node is called once and then propagates.
        model = FlakyModel(fail_times=1, response="ok")
        runnable = compile_routine_graph(graph, model=model)
        with pytest.raises(RuntimeError, match="simulated transient failure"):
            await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})
        assert model.attempts == 1

    async def test_max_retries_n_produces_n_retries_after_first_failure(self):
        """max_retries: 2 means 2 retries, so 3 total attempts before the
        error edge routes to recovery."""
        payload = _single_prompt_step_payload(node_retry={"max_retries": 2, "initial_interval": 0})
        payload["nodes"].append(
            {
                "id": "recover",
                "kind": "eval",
                "output_key": "recover",
                "config": {"expression": "'recovered'"},
            }
        )
        payload["edges"].append({"from": "step", "to": "recover", "level": "error"})
        graph = validate_routine_graph_payload(payload)
        model = FlakyModel(fail_times=999, response="ok")
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})
        assert model.attempts == 3
        assert result["data"]["recover"] == "recovered"

    def test_retry_omitted_jitter_defaults_to_true(self):
        """A retry block that says nothing about jitter still gets it, so
        sibling branches that failed together do not retry in lockstep."""
        payload = _single_prompt_step_payload(node_retry={"max_retries": 1, "initial_interval": 0})
        graph = validate_routine_graph_payload(payload)
        compiled = compile_routine_graph(graph, model=FakeModel([]))
        assert compiled.nodes["step"].retry_policy[0].jitter is True


class TestRecursionLimitSetting:
    """settings.recursion_limit is the real backstop for a graph cycle that never terminates."""

    async def test_settings_recursion_limit_stops_an_unconditional_cycle(self):
        payload = {
            "id": "cycle",
            "name": "cycle",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "a", "config": {"expression": "1"}},
                {"id": "b", "kind": "eval", "output_key": "b", "config": {"expression": "1"}},
            ],
            "edges": [
                {"from": "a", "to": "b", "level": "normal"},
                {"from": "b", "to": "a", "level": "normal"},
            ],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(
            graph, model=FakeModel([]), execution_policy=PolicyValues(graph_step_limit=6)
        )
        with pytest.raises(GraphRecursionError):
            await runnable.ainvoke({"messages": [HumanMessage(content="go")]})


_COMPLETION_SCHEMA = {
    "type": "object",
    "properties": {"completion_pct": {"type": "number"}},
    "required": ["completion_pct"],
    "additionalProperties": False,
}

_RESULT_SCHEMA = {
    "type": "object",
    "properties": {"result": {"type": "boolean"}},
    "required": ["result"],
    "additionalProperties": False,
}


def _single_prompt_step_payload_with_schema(response_schema: dict) -> dict:
    config = {"prompt_template": "go", "response_schema": response_schema}
    return {
        "id": "structured-step",
        "name": "structured-step",
        "description": "",
        "version": "2.0.0",
        "entry": "step",
        "nodes": [{"id": "step", "kind": "prompt", "output_key": "step", "config": config}],
        "edges": [],
    }


def _single_classify_step_payload_with_schema(response_schema: dict) -> dict:
    config = {"rubric_prompt": "grade this", "response_schema": response_schema}
    return {
        "id": "structured-gate",
        "name": "structured-gate",
        "description": "",
        "version": "2.0.0",
        "entry": "gate",
        "nodes": [{"id": "gate", "kind": "classify", "output_key": "gate", "config": config}],
        "edges": [],
    }


class TestResponseSchema:
    """config.response_schema on prompt/classify nodes binds
    model.with_structured_output(schema, method='json_schema', strict=True) instead
    of a plain invoke -- Responses-API schema-constrained generation, not text-parsed
    after the fact."""

    async def test_prompt_node_with_response_schema_writes_validated_dict_to_output_key(self):
        payload = _single_prompt_step_payload_with_schema(_COMPLETION_SCHEMA)
        graph = validate_routine_graph_payload(payload)
        model = FakeStructuredModel([{"completion_pct": 0.62}])
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

        assert result["data"]["step"] == {"completion_pct": 0.62}
        assert "step_json" not in result["data"]
        assert model.bind_calls == [
            {
                "schema": {"title": "RoutineResponse", **_COMPLETION_SCHEMA},
                "method": "json_schema",
                "strict": True,
                "include_raw": True,
            }
        ]

    async def test_structured_response_preserves_authored_schema_title(self):
        schema = {"title": "AuthorResponse", **_COMPLETION_SCHEMA}
        graph = validate_routine_graph_payload(_single_prompt_step_payload_with_schema(schema))
        model = FakeStructuredModel([{"completion_pct": 1.0}])
        await compile_routine_graph(graph, model=model).ainvoke({"data": {}})
        assert model.bind_calls[0]["schema"]["title"] == "AuthorResponse"
        assert "title" not in _COMPLETION_SCHEMA

    async def test_prompt_node_with_response_schema_raises_on_validation_failure(self):
        payload = _single_prompt_step_payload_with_schema(_COMPLETION_SCHEMA)
        graph = validate_routine_graph_payload(payload)
        model = FakeStructuredModel([None])
        runnable = compile_routine_graph(graph, model=model)
        with pytest.raises(ValueError, match="structured response failed schema validation"):
            await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

    async def test_classify_node_with_response_schema_reads_the_result_field(self):
        payload = _single_classify_step_payload_with_schema(_RESULT_SCHEMA)
        graph = validate_routine_graph_payload(payload)
        model = FakeStructuredModel([{"result": True}])
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result["data"]["gate"] == {"result": True}

    async def test_classify_node_with_response_schema_false_result(self):
        payload = _single_classify_step_payload_with_schema(_RESULT_SCHEMA)
        graph = validate_routine_graph_payload(payload)
        model = FakeStructuredModel([{"result": False}])
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result["data"]["gate"] == {"result": False}


def _single_prompt_step_payload_with_ref(
    ref_schema: dict, *, defs: dict[str, dict] | None = None
) -> dict:
    payload = _single_prompt_step_payload_with_schema(ref_schema)
    payload["$defs"] = defs if defs is not None else {"completion": _COMPLETION_SCHEMA}
    return payload


class TestResponseSchemaRef:
    """config.response_schema may be {"$ref": "#/$defs/<name>"} instead of an inline
    schema, resolved against the routine graph document's own top-level $defs --
    reuse across a routine's nodes without repeating the same schema."""

    async def test_ref_resolves_against_graph_defs_and_reaches_the_bound_model(self):
        payload = _single_prompt_step_payload_with_ref({"$ref": "#/$defs/completion"})
        graph = validate_routine_graph_payload(payload)
        model = FakeStructuredModel([{"completion_pct": 0.5}])
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

        assert result["data"]["step"] == {"completion_pct": 0.5}
        assert model.bind_calls[0]["schema"] == {"title": "RoutineResponse", **_COMPLETION_SCHEMA}

    async def test_ref_to_an_undefined_name_raises_valueerror(self):
        payload = _single_prompt_step_payload_with_ref(
            {"$ref": "#/$defs/missing"}, defs={"completion": _COMPLETION_SCHEMA}
        )
        graph = validate_routine_graph_payload(payload)
        with pytest.raises(ValueError, match="undefined '\\$defs.missing'"):
            compile_routine_graph(graph, model=FakeStructuredModel([]))

    async def test_malformed_ref_shape_raises_valueerror(self):
        payload = _single_prompt_step_payload_with_ref(
            {"$ref": "completion"}, defs={"completion": _COMPLETION_SCHEMA}
        )
        graph = validate_routine_graph_payload(payload)
        with pytest.raises(ValueError, match="must be of the form '#/\\$defs/<name>'"):
            compile_routine_graph(graph, model=FakeStructuredModel([]))


class TestInputSchema:
    """input_schema declares extra top-level channels a caller can pass alongside
    messages; the seed node copies each into data so an expression can reference
    it directly by name."""

    async def test_declared_field_reaches_data_and_messages_survive(self):
        payload = {
            "id": "echo",
            "name": "echo",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {
                    "id": "a",
                    "kind": "eval",
                    "output_key": "echo",
                    "config": {"expression": "candidate"},
                }
            ],
            "edges": [],
            "input_schema": {"type": "object", "properties": {"candidate": {"type": "string"}}},
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke(
            {"messages": [HumanMessage(content="go")], "candidate": "hello world"}
        )
        assert result["data"]["candidate"] == "hello world"
        assert result["data"]["echo"] == "hello world"
        assert result["messages"][0].content == "go"

    async def test_undeclared_field_is_silently_dropped(self):
        # No input_schema declared: only messages/data survive the input filter,
        # matching every pre-existing routine's behavior (regression guard).
        payload = {
            "id": "echo",
            "name": "echo",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "a", "config": {"expression": "1"}}
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke(
            {"messages": [HumanMessage(content="go")], "candidate": "hello"}
        )
        assert "candidate" not in result
        assert "candidate" not in result["data"]


class TestOutputSchema:
    """output_schema natively projects the compiled graph's invoke() result down to
    {messages, **declared fields} via an auto-inserted terminal projector node."""

    async def test_projects_only_declared_fields_plus_messages(self):
        payload = {
            "id": "project",
            "name": "project",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "kept", "config": {"expression": "1"}},
                {"id": "b", "kind": "eval", "output_key": "dropped", "config": {"expression": "2"}},
            ],
            "edges": [{"from": "a", "to": "b", "level": "normal"}],
            "output_schema": {"properties": {"kept": {"type": "integer"}}},
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result == {"messages": result["messages"], "kept": 1}
        assert result["messages"][0].content == "go"

    async def test_field_missing_at_end_of_run_is_omitted_not_erroring(self):
        payload = {
            "id": "partial",
            "name": "partial",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "a", "config": {"expression": "1"}}
            ],
            "edges": [],
            "output_schema": {
                "properties": {"a": {"type": "integer"}, "never_produced": {"type": "string"}}
            },
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result["a"] == 1
        assert "never_produced" not in result

    async def test_no_output_schema_returns_full_internal_state(self):
        # Regression guard: the common case (no output_schema) is untouched --
        # 'data' and every field it accumulated still come back whole.
        payload = {
            "id": "full",
            "name": "full",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "a", "config": {"expression": "1"}}
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result["data"]["a"] == 1


class TestRoutineDelegationContract:
    """_build_routine_node/_build_agent_node and subagent.py's CompiledSubAgent
    proxy both unconditionally read result["messages"] from a compiled routine's
    invoke() -- an output_schema that dropped messages would silently break
    delegation for any routine invoked as a subagent."""

    async def test_output_schema_still_returns_messages_for_delegation(self):
        payload = {
            "id": "delegate-target",
            "name": "delegate-target",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "result", "config": {"expression": "1"}}
            ],
            "edges": [],
            "output_schema": {"properties": {"result": {"type": "integer"}}},
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="delegated call")]})
        # Mirrors _build_routine_node's own access pattern exactly.
        target_messages = result.get("messages") or []
        assert target_messages, "messages must survive output_schema projection"
        assert target_messages[0].content == "delegated call"


class TestRunMetaInstrumentation:
    """Every node kind gets wall-clock duration recorded into run_meta,
    regardless of what it returns -- a single choke point at graph
    construction time, not a per-kind opt-in a node builder could omit."""

    async def test_single_node_records_duration(self):
        payload = {
            "id": "timed",
            "name": "timed",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {
                    "id": "a",
                    "kind": "eval",
                    "output_key": "result",
                    "config": {"expression": "1 + 1"},
                }
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

        assert set(result["run_meta"]) == {"a"}
        entries = result["run_meta"]["a"]
        assert len(entries) == 1
        assert entries[0]["duration_ms"] >= 0
        assert "started_at" in entries[0]
        # The node's own return value is untouched alongside run_meta.
        assert result["data"]["result"] == 2

    async def test_multiple_nodes_each_get_their_own_entry(self):
        payload = {
            "id": "timed-chain",
            "name": "timed-chain",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "a", "config": {"expression": "1"}},
                {"id": "b", "kind": "eval", "output_key": "b", "config": {"expression": "2"}},
            ],
            "edges": [{"from": "a", "to": "b", "level": "normal"}],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

        assert set(result["run_meta"]) == {"a", "b"}
        assert len(result["run_meta"]["a"]) == 1
        assert len(result["run_meta"]["b"]) == 1

    async def test_looped_node_accumulates_one_entry_per_iteration(self):
        payload = {
            "id": "timed-loop",
            "name": "timed-loop",
            "description": "",
            "version": "2.0.0",
            "entry": "count",
            "nodes": [
                {
                    "id": "count",
                    "kind": "eval",
                    "output_key": "count",
                    "config": {"expression": "count + 1", "defaults": {"count": 0}},
                },
            ],
            "edges": [
                {
                    "from": "count",
                    "to": "count",
                    "level": "normal",
                    "loop_guard": {"expression": "count < 3"},
                }
            ],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

        # "count" ran 3 times (loop_guard false on the 3rd) -- run_meta
        # accumulated one entry per iteration, not just the final one.
        assert len(result["run_meta"]["count"]) == 3
        assert result["data"]["count"] == 3

    async def test_retry_only_records_the_attempt_that_actually_completed(self):
        """A raised exception never produces a state update to merge (that's
        LangGraph's own retry/state model, not a choice this wrapper makes)
        -- so failed attempts can't be recorded via this state-channel
        mechanism, only the attempt that actually returned."""
        payload = _single_prompt_step_payload(node_retry={"max_retries": 2, "initial_interval": 0})
        graph = validate_routine_graph_payload(payload)
        model = FlakyModel(fail_times=2, response="ok")
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})

        assert model.attempts == 3
        assert len(result["run_meta"]["step"]) == 1

    async def test_prompt_node_records_token_usage(self):
        """A real LLM call's usage_metadata lands under run_meta[node]['tokens']."""
        payload = _single_prompt_step_payload()
        graph = validate_routine_graph_payload(payload)
        model = FakeModel(
            ["hi"], usage=[{"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}]
        )
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})

        assert result["run_meta"]["step"][0]["tokens"] == {
            "input_tokens": 12,
            "output_tokens": 4,
            "total_tokens": 16,
        }

    async def test_classify_node_records_token_usage(self):
        """classify threads its raw structured-output AIMessage through
        `messages` (see _build_classify_node) purely so this uniform
        extraction sees its usage too -- classify's own return value never
        exposed `messages` before this."""
        payload = _single_classify_step_payload_with_schema(_RESULT_SCHEMA)
        graph = validate_routine_graph_payload(payload)
        model = FakeStructuredModel(
            [{"result": True}], usage=[{"input_tokens": 5, "output_tokens": 1, "total_tokens": 6}]
        )
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})

        assert result["run_meta"]["gate"][0]["tokens"] == {
            "input_tokens": 5,
            "output_tokens": 1,
            "total_tokens": 6,
        }
        assert result["data"]["gate"] == {"result": True}

    async def test_node_with_no_llm_call_reports_no_tokens_key(self):
        """eval never calls a model -- run_meta has no 'tokens' key at all,
        not a zeroed one, distinguishing "no LLM call" from "unmetered"."""
        payload = {
            "id": "timed",
            "name": "timed",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "result", "config": {"expression": "1"}}
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

        assert "tokens" not in result["run_meta"]["a"][0]

    async def test_llm_call_with_unreported_usage_also_omits_tokens_key(self):
        """The provider not reporting usage_metadata (FakeModel's default)
        must not be conflated with zero tokens spent."""
        payload = _single_prompt_step_payload()
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel(["hi"]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})

        assert "tokens" not in result["run_meta"]["step"][0]

    async def test_agent_node_records_token_usage(self):
        """agent delegates to a compiled sub-agent whose own last message
        carries usage_metadata -- preserved even though the node
        reconstructs its own AIMessage from the extracted text (see
        _message_with_usage)."""
        config = {"agent_name": "repo-manager", "prompt_template": "{request}"}
        payload = {
            "id": "agent-tokens",
            "name": "agent-tokens",
            "description": "",
            "version": "2.0.0",
            "entry": "call",
            "nodes": [{"id": "call", "kind": "agent", "output_key": "result", "config": config}],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        agent = FakeAgent(
            "done", usage={"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}
        )
        runnable = compile_routine_graph(
            graph, model=FakeModel([]), agent_registry={"repo-manager": agent}
        )
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

        assert result["run_meta"]["call"][0]["tokens"] == {
            "input_tokens": 20,
            "output_tokens": 8,
            "total_tokens": 28,
        }


class TestSchemaStateChannels:
    """Declaring input_schema/output_schema fields must keep the run_meta and
    node_outputs channels (contributed by the node callables' RoutineState
    annotations) so per-node instrumentation and reduce nodes keep working."""

    async def test_run_meta_channel_exists_with_input_and_output_schema(self):
        payload = {
            "id": "schema-timed",
            "name": "schema-timed",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "a", "config": {"expression": "1"}},
                {"id": "b", "kind": "eval", "output_key": "b", "config": {"expression": "2"}},
            ],
            "edges": [{"from": "a", "to": "b", "level": "normal"}],
            "input_schema": {"type": "object", "properties": {"candidate": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"b": {"type": "integer"}}},
        }
        graph = validate_routine_graph_payload(payload)
        compiled = compile_routine_graph(graph, model=FakeModel([]))
        # The channels dict proves LangGraph sees run_meta and node_outputs
        # even when the state type is extended for declared schema fields.
        assert "run_meta" in compiled.channels
        assert "node_outputs" in compiled.channels
        # run_meta is a state channel, so it survives even though the
        # projected output only returns messages + declared output fields.
        result = await compiled.ainvoke(
            {"messages": [HumanMessage(content="go")], "candidate": "hello"}
        )
        assert "b" in result

    async def test_node_outputs_channel_exists_with_schema_for_reduce(self):
        payload = {
            "id": "schema-reduce",
            "name": "schema-reduce",
            "description": "",
            "version": "2.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "eval", "output_key": "a", "config": {"expression": "1"}},
                {"id": "b", "kind": "eval", "output_key": "b", "config": {"expression": "2"}},
                {
                    "id": "collect",
                    "kind": "reduce",
                    "output_key": "sum",
                    "config": {"source_node": "a", "operation": "sum"},
                },
            ],
            "edges": [
                {"from": "a", "to": "b", "level": "normal"},
                {"from": "b", "to": "collect", "level": "normal"},
            ],
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {"sum": {"type": "integer"}}},
        }
        graph = validate_routine_graph_payload(payload)
        compiled = compile_routine_graph(graph, model=FakeModel([]))
        assert "node_outputs" in compiled.channels
        result = await compiled.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result["sum"] == 1


class TestNestedRoutineRecursionGuard:
    """Self-referencing or deeply nested routines must fail fast instead of
    recursing unbounded."""

    async def test_self_referencing_routine_raises_cycle_error(self):
        payload = {
            "id": "self-ref",
            "name": "self-ref",
            "description": "",
            "version": "2.0.0",
            "entry": "call",
            "nodes": [
                {
                    "id": "call",
                    "kind": "routine",
                    "output_key": "result",
                    "config": {"routine_name": "self-ref"},
                }
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        reg: dict[str, Any] = {}
        runnable = compile_routine_graph(graph, model=FakeModel([]), routine_registry=reg)
        reg["self-ref"] = runnable
        with pytest.raises(ValueError, match="cycle detected"):
            await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

    async def test_depth_cap_raises_error(self):
        """A chain of N distinct routines where N exceeds the cap must fail."""
        ids = [f"r{i}" for i in range(12)]
        reg: dict[str, Any] = {}
        for i, rid in enumerate(ids):
            payload = {
                "id": rid,
                "name": rid,
                "description": "",
                "version": "2.0.0",
                "entry": "call",
                "nodes": [
                    {
                        "id": "call",
                        "kind": "routine",
                        "output_key": "result",
                        "config": {"routine_name": ids[i + 1] if i + 1 < len(ids) else "r0"},
                    }
                ],
                "edges": [],
            }
            reg[rid] = compile_routine_graph(
                validate_routine_graph_payload(payload), model=FakeModel([]), routine_registry=reg
            )
        with pytest.raises(ValueError, match="depth exceeds limit"):
            await reg["r0"].ainvoke({"messages": [HumanMessage(content="go")]})


class TestA2ANodeTimeout:
    """The A2A node must pass the resolved node/policy timeout to the client."""

    async def test_a2a_node_passes_configured_timeout(self):
        from unittest.mock import AsyncMock, Mock, patch

        config = {
            "agent_name": "peer",
            "prompt_template": "{request}",
            "timeout": 5,
        }
        payload = {
            "id": "a2a-timeout",
            "name": "a2a-timeout",
            "description": "",
            "version": "2.0.0",
            "entry": "call",
            "nodes": [{"id": "call", "kind": "a2a", "output_key": "result", "config": config}],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(
            graph,
            model=FakeModel([]),
            tool_registry=Mock(
                find_by_name=Mock(return_value={"transport": "http", "url": "http://peer"})
            ),
        )
        with patch(
            "app.progenitor.a2a.client.A2AClient.call_agent",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "result": {"status": {"message": {"parts": [{"text": "ok"}]}}},
            },
        ) as mock_call:
            await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
            assert mock_call.call_count == 1
            _, kwargs = mock_call.call_args
            assert kwargs["timeout"] == 5.0

    async def test_a2a_node_passes_policy_default_timeout(self):
        from unittest.mock import AsyncMock, Mock, patch

        config = {"agent_name": "peer", "prompt_template": "{request}"}
        payload = {
            "id": "a2a-policy-timeout",
            "name": "a2a-policy-timeout",
            "description": "",
            "version": "2.0.0",
            "entry": "call",
            "nodes": [{"id": "call", "kind": "a2a", "output_key": "result", "config": config}],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(
            graph,
            model=FakeModel([]),
            tool_registry=Mock(
                find_by_name=Mock(return_value={"transport": "http", "url": "http://peer"})
            ),
            execution_policy=PolicyValues(attempt_timeout=42),
        )
        with patch(
            "app.progenitor.a2a.client.A2AClient.call_agent",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "result": {"status": {"message": {"parts": [{"text": "ok"}]}}},
            },
        ) as mock_call:
            await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
            assert mock_call.call_count == 1
            _, kwargs = mock_call.call_args
            assert kwargs["timeout"] == 42.0

    async def test_a2a_node_zero_timeout_uses_no_limit_cap(self):
        from unittest.mock import AsyncMock, Mock, patch

        config = {"agent_name": "peer", "prompt_template": "{request}", "timeout": 0}
        payload = {
            "id": "a2a-unlimited",
            "name": "a2a-unlimited",
            "description": "",
            "version": "2.0.0",
            "entry": "call",
            "nodes": [{"id": "call", "kind": "a2a", "output_key": "result", "config": config}],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(
            graph,
            model=FakeModel([]),
            tool_registry=Mock(
                find_by_name=Mock(return_value={"transport": "http", "url": "http://peer"})
            ),
        )
        with patch(
            "app.progenitor.a2a.client.A2AClient.call_agent",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "result": {"status": {"message": {"parts": [{"text": "ok"}]}}},
            },
        ) as mock_call:
            await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
            assert mock_call.call_count == 1
            _, kwargs = mock_call.call_args
            from app.progenitor.routines.delegation_nodes import _A2A_NO_LIMIT_TIMEOUT

            assert kwargs["timeout"] == _A2A_NO_LIMIT_TIMEOUT
