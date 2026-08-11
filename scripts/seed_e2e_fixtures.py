#!/usr/bin/env python3
"""Seed the routine fixtures the Playwright suite navigates to directly.

`app/frontend/e2e/*.spec.js` opens these by id. The three `routine-*` graphs
plus `routine-private-context` are fixtures rather than demo content, and the
specs that use them do not skip-guard, so a database without them fails the
suite outright rather than skipping.

Seeding goes through `AgentStore.ensure_routine_agent` -- the same path the
runtime uses when a chat delegation authors a routine graph -- so a fixture is
stored exactly the way a real one would be. Re-running is safe: `ensure_` only
creates what is missing.

    python scripts/seed_e2e_fixtures.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DATABASE_URL = "postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor"

# --- routine-branch-join: a fork with a real join (mirrors feedback-categoriser's shape). ---
branch_join_graph = {
    "id": "routine-branch-join",
    "name": "Routine Branch Join",
    "version": "2.0.0",
    "description": "E2E fixture: a router fork with two real branches converging at a fan-in join.",
    "settings": {},
    "entry": "classify",
    "nodes": [
        {
            "id": "classify",
            "kind": "prompt",
            "label": "Classify Request",
            "description": "Classify the incoming request.",
            "config": {
                "prompt_template": "Classify {request}. Return a JSON object with field 'category' as 'urgent' or 'routine'.",
                "response_schema": {
                    "type": "object",
                    "required": ["category"],
                    "properties": {"category": {"type": "string", "enum": ["urgent", "routine"]}},
                },
            },
            "output_key": "classification",
        },
        {
            "id": "handle_urgent",
            "kind": "prompt",
            "label": "Handle Urgent",
            "description": "Respond to an urgent request.",
            "config": {"prompt_template": "Write a brief urgent-priority acknowledgement."},
            "output_key": "urgent_reply",
        },
        {
            "id": "handle_routine",
            "kind": "prompt",
            "label": "Handle Routine",
            "description": "Respond to a routine request.",
            "config": {"prompt_template": "Write a brief standard-priority acknowledgement."},
            "output_key": "routine_reply",
        },
        {
            "id": "combine",
            "kind": "prompt",
            "label": "Combine Reply",
            "description": "Combine whichever branch ran into one reply.",
            "config": {
                "prompt_template": "Combine {urgent_reply} or {routine_reply} into a final reply."
            },
            "output_key": "combine",
        },
        {
            "id": "noop",
            "kind": "noop",
            "label": "Join fan-out",
            "description": "",
            "config": {},
            "output_key": "noop",
        },
    ],
    "edges": [
        {
            "from": "classify",
            "level": "route",
            "router": "state_equals",
            "state_key": "classification.category",
            "branches": {"urgent": "handle_urgent", "routine": "handle_routine"},
        },
        {"from": ["handle_urgent", "handle_routine"], "to": "combine", "level": "fan_in"},
        {"from": "combine", "to": "noop", "level": "normal"},
    ],
}

# --- routine-all-controls: a for-each whose body contains a whole fork. ---
all_controls_graph = {
    "id": "routine-all-controls",
    "name": "Routine All Controls",
    "version": "2.0.0",
    "description": "E2E fixture: a Send fan-out foreach whose body contains its own router fork with a join.",
    "settings": {},
    "entry": "collect_items",
    "nodes": [
        {
            "id": "collect_items",
            "kind": "foreach",
            "label": "Collect Items",
            "description": "Fan out over each item.",
            "config": {"mode": "send", "items_from": "items", "item_key": "item"},
            "output_key": "collect_items",
        },
        {
            "id": "classify_item",
            "kind": "prompt",
            "label": "Classify Item",
            "description": "Classify one item.",
            "config": {
                "prompt_template": "Classify {item}. Return a JSON object with field 'kind' as 'a' or 'b'.",
                "response_schema": {
                    "type": "object",
                    "required": ["kind"],
                    "properties": {"kind": {"type": "string", "enum": ["a", "b"]}},
                },
            },
            "output_key": "item_kind",
        },
        {
            "id": "handle_a",
            "kind": "prompt",
            "label": "Handle Kind A",
            "description": "Handle a kind-a item.",
            "config": {"prompt_template": "Process a kind-a item."},
            "output_key": "a_result",
        },
        {
            "id": "handle_b",
            "kind": "prompt",
            "label": "Handle Kind B",
            "description": "Handle a kind-b item.",
            "config": {"prompt_template": "Process a kind-b item."},
            "output_key": "b_result",
        },
        {
            "id": "combine_item",
            "kind": "prompt",
            "label": "Combine Item Result",
            "description": "Combine whichever branch ran into one result.",
            "config": {"prompt_template": "Combine {a_result} or {b_result} into one item result."},
            "output_key": "combine_item",
        },
        {
            "id": "noop",
            "kind": "noop",
            "label": "Join fan-out",
            "description": "",
            "config": {},
            "output_key": "noop",
        },
        {
            "id": "finalize",
            "kind": "prompt",
            "label": "Finalize",
            "description": "Summarize every item's result.",
            "config": {"prompt_template": "Summarize all item results into one report."},
            "output_key": "finalize",
        },
    ],
    "edges": [
        {"from": "collect_items", "to": "classify_item", "level": "normal"},
        {
            "from": "classify_item",
            "level": "route",
            "router": "state_equals",
            "state_key": "item_kind.kind",
            "branches": {"a": "handle_a", "b": "handle_b"},
        },
        {"from": ["handle_a", "handle_b"], "to": "combine_item", "level": "fan_in"},
        {"from": "combine_item", "to": "noop", "level": "normal"},
        {"from": "noop", "to": "finalize", "level": "normal"},
    ],
}

# --- routine-condition-gate: a condition node's fixed true/false fork. ---
condition_gate_graph = {
    "id": "routine-condition-gate",
    "name": "routine-condition-gate",
    "edges": [
        {"to": "gate", "from": "score", "level": "normal"},
        {
            "from": "gate",
            "level": "route",
            "router": "state_equals",
            "branches": {"true": "handle_long", "false": "handle_short"},
            "state_key": "gate",
        },
        {"to": "combine", "from": ["handle_long", "handle_short"], "level": "fan_in"},
        {"to": "noop", "from": "combine", "level": "normal"},
    ],
    "entry": "score",
    "nodes": [
        {
            "id": "score",
            "kind": "eval",
            "label": "Score Request",
            "config": {"expression": "len(request)"},
            "output_key": "score",
            "description": "Produce a numeric score to gate on.",
        },
        {
            "id": "gate",
            "kind": "condition",
            "label": "Long Enough",
            "config": {"inputs": {"score": "score"}, "expression": "score > 20"},
            "output_key": "gate",
            "description": "Two-way gate: the fork below is fixed to true/false.",
        },
        {
            "id": "handle_long",
            "kind": "prompt",
            "label": "Handle Long",
            "config": {"prompt_template": "Summarise the long request."},
            "output_key": "long_reply",
            "description": "The true arm.",
        },
        {
            "id": "handle_short",
            "kind": "prompt",
            "label": "Handle Short",
            "config": {"prompt_template": "Answer the short request directly."},
            "output_key": "short_reply",
            "description": "The false arm.",
        },
        {
            "id": "combine",
            "kind": "prompt",
            "label": "Combine Reply",
            "config": {"prompt_template": "Combine {long_reply} or {short_reply} into a reply."},
            "output_key": "combine",
            "description": "Combine whichever arm ran.",
        },
        {
            "id": "noop",
            "kind": "noop",
            "label": "Done",
            "config": {},
            "output_key": "noop",
            "description": "",
        },
    ],
    "version": "2.0.0",
    "description": "",
}

# --- routine-private-context: a plain linear chain, so a fan-out inserted
# mid-chain makes any swallowed step obvious as a reorder. ---
private_context_graph = {
    "id": "routine-private-context",
    "name": "Routine Private Context",
    "version": "2.0.0",
    "description": (
        "E2E fixture: a plain linear chain, so a fan-out inserted mid-chain "
        "makes any swallowed step obvious as a reorder."
    ),
    "settings": {},
    "entry": "inject_context",
    "nodes": [
        {
            "id": "inject_context",
            "kind": "prompt",
            "label": "Inject context",
            "description": "Seed the run with the caller's private context.",
            "config": {"prompt_template": "Summarise the private context in {request}."},
            "output_key": "context",
        },
        {
            "id": "build_private_trace",
            "kind": "prompt",
            "label": "Build private trace",
            "description": "Build a trace that never leaves the routine.",
            "config": {"prompt_template": "Build a private trace from {context}."},
            "output_key": "trace",
        },
        {
            "id": "run_shared_subgraph",
            "kind": "routine",
            "label": "Run shared subgraph",
            "description": "Hand the trace to a shared routine.",
            "config": {"routine_name": "feedback-categoriser"},
            "output_key": "shared",
        },
        {
            "id": "return_public_answer",
            "kind": "prompt",
            "label": "Return public answer",
            "description": "Answer with only the shareable part.",
            "config": {"prompt_template": "Answer using {shared}, omitting the private trace."},
            "output_key": "answer",
        },
    ],
    "edges": [
        {"from": "inject_context", "to": "build_private_trace", "level": "normal"},
        {"from": "build_private_trace", "to": "run_shared_subgraph", "level": "normal"},
        {"from": "run_shared_subgraph", "to": "return_public_answer", "level": "normal"},
    ],
}

FIXTURES = (
    branch_join_graph,
    all_controls_graph,
    condition_gate_graph,
    private_context_graph,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    args = parser.parse_args()

    # Imported here rather than at module scope: the app package is only
    # importable once PROJECT_ROOT is on sys.path above.
    from app.backend.agent_store import AgentStore
    from app.backend.document_store import PostgresDocumentStore
    from app.progenitor.routines.graph import validate_routine_graph_payload

    for graph in FIXTURES:
        validate_routine_graph_payload(graph)
        print(f"{graph['id']}: valid")

    doc_store = PostgresDocumentStore(args.database_url)
    doc_store.open()
    try:
        store = AgentStore(args.database_url, doc_store)
        store.open()
        for graph in FIXTURES:
            # The graph document is rewritten either way; the flag only says
            # whether the agent roster gained an entry it did not have.
            listed = store.ensure_routine_agent(graph["id"], graph)
            print(f"{graph['id']}: graph written{', agent registered' if listed else ''}")
    finally:
        doc_store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
