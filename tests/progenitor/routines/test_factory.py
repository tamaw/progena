"""Tests for compiling and running a RoutineGraph (app.progenitor.routines.factory)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

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
    """Returns queued responses in order, one per ``.ainvoke`` call."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self._responses.pop(0))


class FakeStructuredModel:
    """Stands in for a BaseChatModel whose ``with_structured_output(...)`` is
    exercised (method='json_schema', strict=True, include_raw=True) -- returns
    queued parsed values in order, one per bound Runnable's ``.ainvoke`` call.
    A queued ``None`` simulates a schema-validation failure."""

    def __init__(self, parsed_responses: list[dict | None]):
        self._responses = list(parsed_responses)
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
                raw = AIMessage(content=json.dumps(parsed) if parsed is not None else "")
                error = None if parsed is not None else ValueError("simulated schema violation")
                return {"raw": raw, "parsed": parsed, "parsing_error": error}

        return _StructuredRunnable()


class FakeAgent:
    """Stands in for the ``agent_registry["repo-manager"]`` compiled target."""

    def __init__(self, response: str):
        self._response = response
        self.calls: list[dict] = []

    async def ainvoke(self, state):
        self.calls.append(state)
        return {"messages": [AIMessage(content=self._response)]}


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
            "version": "1.0.0",
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
        runnable = compile_routine_graph(
            graph, model=model, subagent_registry={"helper": FakeSubgraph()}
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
        config["retry"] = node_retry
    return {
        "id": "flaky-step",
        "name": "flaky-step",
        "description": "",
        "version": "1.0.0",
        "entry": "step",
        "nodes": [{"id": "step", "kind": "prompt", "output_key": "step", "config": config}],
        "edges": [],
        "settings": settings or {},
    }


class TestGraphWideRetryDefault:
    """settings.retry applies via StateGraph.set_node_defaults to nodes without their own config.retry."""

    async def test_settings_retry_covers_a_node_with_no_retry_of_its_own(self):
        payload = _single_prompt_step_payload(
            settings={"retry": {"max_attempts": 3, "initial_interval": 0}}
        )
        graph = validate_routine_graph_payload(payload)
        model = FlakyModel(fail_times=2, response="ok")
        runnable = compile_routine_graph(graph, model=model)
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
            settings={"retry": {"max_attempts": 3, "initial_interval": 0}},
            node_retry={"max_attempts": 1, "initial_interval": 0},
        )
        graph = validate_routine_graph_payload(payload)
        model = FlakyModel(fail_times=2, response="ok")
        runnable = compile_routine_graph(graph, model=model)
        with pytest.raises(RuntimeError, match="simulated transient failure"):
            await runnable.ainvoke({"messages": [HumanMessage(content="hi")]})
        assert model.attempts == 1


class TestRecursionLimitSetting:
    """settings.recursion_limit is the real backstop for a graph cycle that never terminates."""

    async def test_settings_recursion_limit_stops_an_unconditional_cycle(self):
        payload = {
            "id": "cycle",
            "name": "cycle",
            "description": "",
            "version": "1.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "code", "output_key": "a", "config": {"expression": "1"}},
                {"id": "b", "kind": "code", "output_key": "b", "config": {"expression": "1"}},
            ],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
            "settings": {"recursion_limit": 6},
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
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
        "version": "1.0.0",
        "entry": "step",
        "nodes": [{"id": "step", "kind": "prompt", "output_key": "step", "config": config}],
        "edges": [],
        "settings": {},
    }


def _single_eval_rubric_step_payload_with_schema(response_schema: dict) -> dict:
    config = {"rubric_prompt": "grade this", "response_schema": response_schema}
    return {
        "id": "structured-gate",
        "name": "structured-gate",
        "description": "",
        "version": "1.0.0",
        "entry": "gate",
        "nodes": [{"id": "gate", "kind": "eval", "output_key": "gate", "config": config}],
        "edges": [],
        "settings": {},
    }


class TestResponseSchema:
    """config.response_schema on prompt/eval nodes binds
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
                "schema": _COMPLETION_SCHEMA,
                "method": "json_schema",
                "strict": True,
                "include_raw": True,
            }
        ]

    async def test_prompt_node_with_response_schema_raises_on_validation_failure(self):
        payload = _single_prompt_step_payload_with_schema(_COMPLETION_SCHEMA)
        graph = validate_routine_graph_payload(payload)
        model = FakeStructuredModel([None])
        runnable = compile_routine_graph(graph, model=model)
        with pytest.raises(ValueError, match="structured response failed schema validation"):
            await runnable.ainvoke({"messages": [HumanMessage(content="go")]})

    async def test_eval_node_with_response_schema_reads_the_result_field(self):
        payload = _single_eval_rubric_step_payload_with_schema(_RESULT_SCHEMA)
        graph = validate_routine_graph_payload(payload)
        model = FakeStructuredModel([{"result": True}])
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result["data"]["gate"] is True

    async def test_eval_node_with_response_schema_false_result(self):
        payload = _single_eval_rubric_step_payload_with_schema(_RESULT_SCHEMA)
        graph = validate_routine_graph_payload(payload)
        model = FakeStructuredModel([{"result": False}])
        runnable = compile_routine_graph(graph, model=model)
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result["data"]["gate"] is False


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
        assert model.bind_calls[0]["schema"] == _COMPLETION_SCHEMA

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
    messages; the seed node copies each into data so config.inputs paths reach them."""

    async def test_declared_field_reaches_data_and_messages_survive(self):
        payload = {
            "id": "echo",
            "name": "echo",
            "description": "",
            "version": "1.0.0",
            "entry": "a",
            "nodes": [
                {
                    "id": "a",
                    "kind": "code",
                    "output_key": "echo",
                    "config": {"expression": "candidate", "inputs": {"candidate": "candidate"}},
                }
            ],
            "edges": [],
            "input_schema": {"candidate": "string"},
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
            "version": "1.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "code", "output_key": "a", "config": {"expression": "1"}}
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
            "version": "1.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "code", "output_key": "kept", "config": {"expression": "1"}},
                {"id": "b", "kind": "code", "output_key": "dropped", "config": {"expression": "2"}},
            ],
            "edges": [{"from": "a", "to": "b"}],
            "output_schema": {"kept": "integer"},
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
            "version": "1.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "code", "output_key": "a", "config": {"expression": "1"}}
            ],
            "edges": [],
            "output_schema": {"a": "integer", "never_produced": "string"},
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
            "version": "1.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "code", "output_key": "a", "config": {"expression": "1"}}
            ],
            "edges": [],
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="go")]})
        assert result["data"]["a"] == 1


class TestSubagentDelegationContract:
    """_build_subagent_node/_build_agent_node and subagent.py's CompiledSubAgent
    proxy both unconditionally read result["messages"] from a compiled routine's
    invoke() -- an output_schema that dropped messages would silently break
    delegation for any routine invoked as a subagent."""

    async def test_output_schema_still_returns_messages_for_delegation(self):
        payload = {
            "id": "delegate-target",
            "name": "delegate-target",
            "description": "",
            "version": "1.0.0",
            "entry": "a",
            "nodes": [
                {"id": "a", "kind": "code", "output_key": "result", "config": {"expression": "1"}}
            ],
            "edges": [],
            "output_schema": {"result": "integer"},
        }
        graph = validate_routine_graph_payload(payload)
        runnable = compile_routine_graph(graph, model=FakeModel([]))
        result = await runnable.ainvoke({"messages": [HumanMessage(content="delegated call")]})
        # Mirrors _build_subagent_node's own access pattern exactly.
        target_messages = result.get("messages") or []
        assert target_messages, "messages must survive output_schema projection"
        assert target_messages[0].content == "delegated call"
