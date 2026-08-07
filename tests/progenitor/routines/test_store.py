"""Tests for routine graph storage-key helpers (app.progenitor.routines.store)."""

from __future__ import annotations

import json

import pytest

from app.progenitor.routines.store import (
    ROUTINE_GRAPH_KEY_PREFIX,
    default_routine_graph,
    parse_routine_graph_document,
    routine_file_name,
    routine_graph_key,
    routine_id_from_path,
)


def test_routine_graph_key_prefixes_id():
    assert routine_graph_key("subagent-observe") == "routine:subagent-observe"
    assert ROUTINE_GRAPH_KEY_PREFIX == "routine:"


def test_routine_file_name():
    assert routine_file_name("subagent-observe") == "subagent-observe.json"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("subagent-observe", "subagent-observe"),
        ("subagent-observe.json", "subagent-observe"),
        ("/routines/subagent-observe.json", "subagent-observe"),
        ("/subagent-observe.json", "subagent-observe"),
    ],
)
def test_routine_id_from_path_normalizes(raw, expected):
    assert routine_id_from_path(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "/"])
def test_routine_id_from_path_rejects_empty(raw):
    with pytest.raises(ValueError):
        routine_id_from_path(raw)


def test_default_routine_graph_is_schema_valid_and_round_trips():
    graph = default_routine_graph("subagent-observe", name="Observe Agent", description="Watches.")
    assert graph["id"] == "subagent-observe"
    assert graph["name"] == "Observe Agent"
    assert graph["entry"] == "prompt"
    # Must itself parse+validate cleanly, proving it satisfies the same schema
    # RoutineDirectoryBackend.awrite enforces.
    parsed = parse_routine_graph_document(json.dumps(graph))
    assert parsed["id"] == "subagent-observe"
    assert parsed["nodes"][0]["id"] == "prompt"


def test_parse_routine_graph_document_preserves_edge_aliases():
    graph = default_routine_graph("edge-agent")
    graph["nodes"].append(
        {
            "id": "done",
            "label": "Done",
            "kind": "noop",
            "output_key": "done",
            "config": {},
        }
    )
    graph["edges"] = [{"from": "prompt", "to": "done", "level": "normal"}]

    parsed = parse_routine_graph_document(json.dumps(graph))

    assert parsed["edges"] == [{"from": "prompt", "to": "done", "level": "normal"}]


def test_default_routine_graph_falls_back_name_to_id():
    graph = default_routine_graph("bare-agent")
    assert graph["name"] == "bare-agent"
    assert graph["description"] == ""


def test_parse_routine_graph_document_rejects_invalid_json():
    with pytest.raises(ValueError, match="invalid JSON"):
        parse_routine_graph_document("not json")


def test_parse_routine_graph_document_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        parse_routine_graph_document("[1, 2, 3]")


def test_parse_routine_graph_document_rejects_unknown_entry():
    graph = default_routine_graph("bad-entry")
    graph["entry"] = "missing"
    with pytest.raises(ValueError, match="entry references unknown node"):
        parse_routine_graph_document(json.dumps(graph))


def test_parse_routine_graph_document_rejects_additional_properties():
    graph = default_routine_graph("extra-field")
    graph["unexpected"] = True
    with pytest.raises(ValueError, match="schema validation failed"):
        parse_routine_graph_document(json.dumps(graph))
