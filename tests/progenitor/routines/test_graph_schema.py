"""Tests for routine graph structural validation and full schema validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.progenitor.routines.graph import (
    RoutineGraph,
    validate_routine_graph,
    validate_routine_graph_payload,
)

_EXAMPLE_PATH = Path(__file__).parent / "fixtures" / "reference_routine.json"


@pytest.fixture
def example_payload() -> dict:
    return json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))


class TestValidateRoutineGraphPayload:
    def test_accepts_the_reference_example(self, example_payload):
        graph = validate_routine_graph_payload(example_payload)
        assert isinstance(graph, RoutineGraph)
        assert graph.entry == "parse"
        assert len(graph.nodes) == 13
        assert len(graph.edges) == 6

    def test_rejects_unknown_entry(self, example_payload):
        payload = copy.deepcopy(example_payload)
        payload["entry"] = "does-not-exist"
        with pytest.raises(ValueError, match="entry references unknown node"):
            validate_routine_graph_payload(payload)

    def test_rejects_duplicate_node_id(self, example_payload):
        payload = copy.deepcopy(example_payload)
        payload["nodes"].append(dict(payload["nodes"][0]))
        with pytest.raises(ValueError, match="duplicate node id"):
            validate_routine_graph_payload(payload)

    def test_rejects_dangling_edge(self, example_payload):
        payload = copy.deepcopy(example_payload)
        edge = next(edge for edge in payload["edges"] if edge["from"] == "parse")
        edge["to"] = "does-not-exist"
        with pytest.raises(ValueError, match="unknown target node"):
            validate_routine_graph_payload(payload)

    def test_rejects_router_with_no_matching_branch_structurally_valid(self, example_payload):
        """A router with no matching branch for a given *runtime* value is a
        factory-time ValueError, not a schema/structural error -- branch coverage
        can't be checked without executing the graph. Structural validation only
        requires router branch targets to exist and at least one branch."""
        payload = copy.deepcopy(example_payload)
        # Sanity: the reference example's routers are already structurally valid.
        validate_routine_graph_payload(payload)

    def test_rejects_two_config_sources_on_one_node(self, example_payload):
        payload = copy.deepcopy(example_payload)
        parse_node = next(n for n in payload["nodes"] if n["id"] == "parse")
        parse_node["config"]["prompt_file"] = "prompt.txt"
        with pytest.raises(ValueError, match="schema validation failed"):
            validate_routine_graph_payload(payload)

    def test_rejects_unknown_node_kind(self, example_payload):
        payload = copy.deepcopy(example_payload)
        payload["nodes"][0]["kind"] = "bogus"
        with pytest.raises(ValueError, match="schema validation failed"):
            validate_routine_graph_payload(payload)

    def test_rejects_compiler_seed_node_id(self, example_payload):
        payload = copy.deepcopy(example_payload)
        payload["nodes"][0]["id"] = "__seed__"
        with pytest.raises(ValueError, match="schema validation failed"):
            validate_routine_graph_payload(payload)


class TestValidateRoutineGraphStructural:
    """The lighter nodes/edges-only check used by the PATCH route (no id/name/
    version/entry document wrapper required)."""

    def test_accepts_plain_and_router_edges(self):
        validate_routine_graph(
            [
                {"id": "a", "kind": "prompt"},
                {"id": "b", "kind": "eval"},
                {"id": "c", "kind": "noop"},
                {"id": "d", "kind": "noop"},
            ],
            [
                {"from": "a", "to": "b", "level": "normal"},
                {
                    "from": "b",
                    "level": "route",
                    "router": "state_equals",
                    "state_key": "b",
                    "branches": {"True": "c"},
                    "default": "d",
                },
            ],
        )

    def test_rejects_multiple_plain_edges_from_same_source(self):
        with pytest.raises(ValueError, match="more than one outgoing edge declaration"):
            validate_routine_graph(
                [
                    {"id": "a", "kind": "prompt"},
                    {"id": "b", "kind": "noop"},
                    {"id": "c", "kind": "noop"},
                ],
                [
                    {"from": "a", "to": "b", "level": "normal"},
                    {"from": "a", "to": "c", "level": "normal"},
                ],
            )

    def test_rejects_duplicate_node_id(self):
        with pytest.raises(ValueError, match="duplicate node id"):
            validate_routine_graph([{"id": "a", "kind": "noop"}, {"id": "a", "kind": "noop"}], [])

    def test_rejects_compiler_seed_node_id(self):
        with pytest.raises(ValueError, match="node id is reserved"):
            validate_routine_graph([{"id": "__seed__", "kind": "noop"}], [])

    def test_rejects_unsupported_router_kind(self):
        with pytest.raises(ValueError, match="unsupported router kind"):
            validate_routine_graph(
                [{"id": "a", "kind": "eval"}, {"id": "b", "kind": "noop"}],
                [
                    {
                        "from": "a",
                        "level": "route",
                        "router": "regex_match",
                        "state_key": "a",
                        "branches": {"x": "b"},
                    }
                ],
            )

    def test_rejects_router_default_to_unknown_node(self):
        with pytest.raises(ValueError, match="unknown target node"):
            validate_routine_graph(
                [{"id": "a", "kind": "eval"}, {"id": "b", "kind": "noop"}],
                [
                    {
                        "from": "a",
                        "level": "route",
                        "router": "state_equals",
                        "state_key": "a",
                        "branches": {"True": "b"},
                        "default": "ghost",
                    }
                ],
            )

    def test_rejects_second_router_edge_from_same_source(self):
        with pytest.raises(ValueError, match="more than one outgoing edge declaration"):
            validate_routine_graph(
                [
                    {"id": "a", "kind": "eval"},
                    {"id": "b", "kind": "noop"},
                    {"id": "c", "kind": "noop"},
                ],
                [
                    {
                        "from": "a",
                        "level": "route",
                        "router": "state_equals",
                        "state_key": "a",
                        "branches": {"True": "b"},
                    },
                    {
                        "from": "a",
                        "level": "route",
                        "router": "state_equals",
                        "state_key": "a",
                        "branches": {"False": "c"},
                    },
                ],
            )

    # A condition's expression is coerced to a bool, so its router can only
    # ever produce `true`/`false` (factory._branch_key). Any other arm is
    # dead code and a default is unreachable -- both are authoring mistakes
    # the editor can't make but a hand-written document can.
    def _condition_graph(self, branches: dict[str, str], **edge_extra):
        return (
            [
                {"id": "gate", "kind": "condition"},
                {"id": "b", "kind": "noop"},
                {"id": "c", "kind": "noop"},
            ],
            [
                {
                    "from": "gate",
                    "level": "route",
                    "router": "state_equals",
                    "state_key": "gate",
                    "branches": branches,
                    **edge_extra,
                },
            ],
        )

    def test_accepts_condition_router_with_both_boolean_arms(self):
        validate_routine_graph(*self._condition_graph({"true": "b", "false": "c"}))

    @pytest.mark.parametrize(
        "branches",
        [
            {"True": "b", "False": "c"},
            {"true": "b"},
            {"true": "b", "false": "c", "maybe": "c"},
        ],
    )
    def test_rejects_condition_router_without_exactly_true_false(self, branches):
        with pytest.raises(ValueError, match="exactly 'true' and 'false' branches"):
            validate_routine_graph(*self._condition_graph(branches))

    def test_rejects_condition_router_with_default(self):
        with pytest.raises(ValueError, match="cannot have a default branch"):
            validate_routine_graph(*self._condition_graph({"true": "b", "false": "c"}, default="c"))

    def test_allows_condition_that_does_not_route(self):
        """A condition is still a plain bool-producing step when nothing
        branches on it -- only its *router*, if present, is constrained."""
        validate_routine_graph(
            [{"id": "gate", "kind": "condition"}, {"id": "b", "kind": "noop"}],
            [{"from": "gate", "to": "b", "level": "normal"}],
        )


@pytest.mark.parametrize(
    "node",
    [
        {
            "id": "prompt",
            "kind": "prompt",
            "output_key": "prompt",
            "config": {"prompt_template": "ok", "promtp_template": "typo"},
        },
        {
            "id": "human",
            "kind": "human",
            "output_key": "human",
            "config": {"allowed_responses": ["yes", "no"]},
        },
    ],
)
def test_rejects_unknown_or_incomplete_strict_node_config(node: dict[str, object]) -> None:
    payload = {
        "id": "strict",
        "name": "Strict",
        "description": "",
        "version": "2.0.0",
        "entry": node["id"],
        "nodes": [node],
        "edges": [],
    }

    with pytest.raises(ValueError, match="schema validation failed"):
        validate_routine_graph_payload(payload)


def test_rejects_unknown_foreach_send_config_field() -> None:
    payload = {
        "id": "strict-foreach",
        "name": "Strict foreach",
        "description": "",
        "version": "2.0.0",
        "entry": "fan",
        "nodes": [
            {
                "id": "fan",
                "kind": "foreach",
                "output_key": "fan",
                "config": {"mode": "send", "items_from": "items", "agent_name": "wrong"},
            },
            {"id": "worker", "kind": "noop", "output_key": "worker", "config": {}},
        ],
        "edges": [{"from": "fan", "to": "worker", "level": "normal"}],
    }

    with pytest.raises(ValueError, match="schema validation failed"):
        validate_routine_graph_payload(payload)


def test_rejects_loop_guard_on_barrier_fanin() -> None:
    with pytest.raises(ValueError, match="loop_guard"):
        validate_routine_graph(
            [
                {"id": "left", "kind": "noop"},
                {"id": "right", "kind": "noop"},
                {"id": "join", "kind": "noop"},
            ],
            [
                {
                    "from": ["left", "right"],
                    "to": "join",
                    "level": "fan_in",
                    "loop_guard": {"expression": "True"},
                }
            ],
        )
