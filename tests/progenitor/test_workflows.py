import pytest

from app.progenitor.workflows import (
    default_workflow_definition,
    ordered_workflow_node_ids,
    validate_workflow_definition_payload,
)


def test_workflow_definition_validator_starts_at_top_level_trigger():
    normalized = validate_workflow_definition_payload(
        default_workflow_definition(title="Validator check")
    )

    assert normalized["title"] == "Validator check"
    assert normalized["trigger"]["event_type"] == "kanban.card.created"
    assert len(normalized["graph"]["nodes"]) == 1


def test_workflow_definition_validator_accepts_references_and_bindings():
    payload = default_workflow_definition()
    prompt = next(iter(payload["graph"]["nodes"].values()))
    prompt["references"] = {
        "agent_id": "reviewer",
        "repository_id": "progenitor",
        "mcp_tool": {"server_id": "github", "tool_name": "create_issue"},
        "skills": [{"source": "native", "name": "managing-mcp"}],
    }
    prompt["inputs"] = {
        "card": {"source": "trigger", "path": "/payload/card"},
        "prior": {"source": "previous_output", "path": "/"},
        "priority": {"source": "literal", "value": "high"},
        "agent": {"source": "reference", "reference": "agent"},
    }

    normalized = validate_workflow_definition_payload(payload)

    node = next(iter(normalized["graph"]["nodes"].values()))
    assert node["references"]["agent_id"] == "reviewer"
    assert node["inputs"]["card"]["path"] == "/payload/card"


def test_workflow_definition_validator_rejects_arbitrary_binding_path():
    payload = default_workflow_definition()
    prompt = next(iter(payload["graph"]["nodes"].values()))
    prompt["inputs"] = {"bad": {"source": "trigger", "path": "payload.card"}}

    with pytest.raises(ValueError, match="binding path"):
        validate_workflow_definition_payload(payload)


def test_workflow_definition_validator_rejects_legacy_specialized_nodes():
    payload = default_workflow_definition()
    payload["graph"]["nodes"]["legacy"] = {
        "kind": "code",
        "title": "Legacy code",
        "language": "python",
        "content": "print('no')",
    }

    with pytest.raises(ValueError, match="schema validation failed"):
        validate_workflow_definition_payload(payload)


def test_workflow_definition_validator_accepts_column_filtered_moved_trigger():
    payload = default_workflow_definition(event_type="kanban.card.moved")
    payload["trigger"]["from_column"] = "backlog"
    payload["trigger"]["to_column"] = "plan"

    normalized = validate_workflow_definition_payload(payload)

    assert normalized["trigger"]["to_column"] == "plan"


def test_workflow_definition_validator_rejects_unknown_column():
    payload = default_workflow_definition(event_type="kanban.card.moved")
    payload["trigger"]["to_column"] = "not-a-real-column"

    with pytest.raises(ValueError, match="schema validation failed"):
        validate_workflow_definition_payload(payload)


def test_workflow_definition_validator_rejects_cycles():
    payload = default_workflow_definition()
    prompt_a_id = next(iter(payload["graph"]["nodes"]))
    prompt_b_id = "prompt_cycle"
    payload["graph"]["nodes"][prompt_b_id] = {
        "kind": "prompt",
        "title": "Cycle prompt",
        "prompt": "Create a cycle",
        "agent_name": "progenitor",
    }
    payload["graph"]["edges"] = [
        {
            "from": {"node": prompt_a_id, "port": "default"},
            "to": {"node": prompt_b_id, "port": "default"},
        },
        {
            "from": {"node": prompt_b_id, "port": "default"},
            "to": {"node": prompt_a_id, "port": "default"},
        },
    ]

    with pytest.raises(ValueError, match="root node|acyclic"):
        validate_workflow_definition_payload(payload)


def test_ordered_workflow_nodes_uses_graph_root_not_event_node():
    payload = default_workflow_definition()
    prompt_a_id = next(iter(payload["graph"]["nodes"]))
    prompt_b_id = "prompt_followup"
    payload["graph"]["nodes"][prompt_b_id] = {
        "kind": "prompt",
        "title": "Follow-up",
        "prompt": "Continue",
        "agent_name": "progenitor",
    }
    payload["graph"]["edges"] = [
        {
            "from": {"node": prompt_a_id, "port": "default"},
            "to": {"node": prompt_b_id, "port": "default"},
        }
    ]
    definition = validate_workflow_definition_payload(payload)

    from app.progenitor.workflows import WorkflowDefinition

    assert ordered_workflow_node_ids(WorkflowDefinition.model_validate(definition)) == [
        prompt_a_id,
        prompt_b_id,
    ]


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("saved_prompt", "prompt_path", "/prompts/review.md"),
        ("agent", "agent_id", "reviewer"),
        ("knowledge", "knowledge_base_id", "architecture"),
        ("repository", "repository_id", "progenitor"),
        ("kanban", "kanban_card_id", "card-42"),
        ("inbox", "inbox_message_id", "message-42"),
        ("routine", "routine_id", "daily-review"),
    ],
)
def test_workflow_definition_validator_accepts_typed_capability_nodes(kind, field, value):
    payload = default_workflow_definition()
    node_id = next(iter(payload["graph"]["nodes"]))
    node = payload["graph"]["nodes"][node_id]
    node.update({"kind": kind, "title": f"Typed {kind}", field: value})
    if kind == "routine":
        node["routine_revision"] = 1

    normalized = validate_workflow_definition_payload(payload)

    assert normalized["graph"]["nodes"][node_id]["kind"] == kind
    assert normalized["graph"]["nodes"][node_id][field] == value
