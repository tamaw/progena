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

_EXAMPLE_PATH = Path(__file__).parent / "fixtures" / "repo_setup_worker.json"


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
        payload["edges"].append({"from": "parse", "to": "does-not-exist"})
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
                {"id": "b", "kind": "code"},
                {"id": "c", "kind": "noop"},
                {"id": "d", "kind": "noop"},
            ],
            [
                {"from": "a", "to": "b"},
                {
                    "from": "b",
                    "router": "state_equals",
                    "state_key": "b",
                    "branches": {"True": "c"},
                    "default": "d",
                },
            ],
        )

    def test_rejects_multiple_plain_edges_from_same_source(self):
        with pytest.raises(ValueError, match="more than one plain edge"):
            validate_routine_graph(
                [
                    {"id": "a", "kind": "prompt"},
                    {"id": "b", "kind": "noop"},
                    {"id": "c", "kind": "noop"},
                ],
                [
                    {"from": "a", "to": "b"},
                    {"from": "a", "to": "c"},
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
                [{"id": "a", "kind": "code"}, {"id": "b", "kind": "noop"}],
                [{"from": "a", "router": "regex_match", "state_key": "a", "branches": {"x": "b"}}],
            )

    def test_rejects_router_default_to_unknown_node(self):
        with pytest.raises(ValueError, match="defaults to unknown node"):
            validate_routine_graph(
                [{"id": "a", "kind": "code"}, {"id": "b", "kind": "noop"}],
                [
                    {
                        "from": "a",
                        "router": "state_equals",
                        "state_key": "a",
                        "branches": {"True": "b"},
                        "default": "ghost",
                    }
                ],
            )

    def test_rejects_second_router_edge_from_same_source(self):
        with pytest.raises(ValueError, match="more than one router edge"):
            validate_routine_graph(
                [
                    {"id": "a", "kind": "code"},
                    {"id": "b", "kind": "noop"},
                    {"id": "c", "kind": "noop"},
                ],
                [
                    {
                        "from": "a",
                        "router": "state_equals",
                        "state_key": "a",
                        "branches": {"True": "b"},
                    },
                    {
                        "from": "a",
                        "router": "state_equals",
                        "state_key": "a",
                        "branches": {"False": "c"},
                    },
                ],
            )
