import pytest

from app.progenitor.schemas import load_schema
from app.progenitor.structured import validate_against_schema
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


@pytest.mark.parametrize(
    ("schema_name", "node"),
    [
        (
            "node_prompt.workflow",
            {
                "kind": "prompt",
                "title": "Prompt",
                "prompt": "Handle the event.",
                "agent_name": "progenitor",
            },
        ),
        (
            "node_saved_prompt.workflow",
            {
                "kind": "saved_prompt",
                "title": "Saved prompt",
                "prompt": "Handle the event.",
                "agent_name": "progenitor",
                "prompt_path": "/prompts/review",
            },
        ),
        (
            "node_agent.workflow",
            {
                "kind": "agent",
                "title": "Agent",
                "prompt": "Delegate the event.",
                "agent_name": "progenitor",
                "agent_id": "reviewer",
            },
        ),
        (
            "node_knowledge.workflow",
            {
                "kind": "knowledge",
                "title": "Knowledge",
                "prompt": "Search for context.",
                "agent_name": "progenitor",
                "knowledge_base_id": "product",
            },
        ),
        (
            "node_repository.workflow",
            {
                "kind": "repository",
                "title": "Repository",
                "prompt": "Review the code.",
                "agent_name": "progenitor",
                "repository_id": "progenitor",
            },
        ),
        (
            "node_kanban.workflow",
            {
                "kind": "kanban",
                "title": "Kanban",
                "prompt": "Handle the card.",
                "agent_name": "progenitor",
                "kanban_card_id": "card-42",
            },
        ),
        (
            "node_ask_user.workflow",
            {
                "kind": "ask_user",
                "title": "Ask user",
                "prompt": "Ask for a decision.",
                "agent_name": "progenitor",
            },
        ),
        (
            "node_inbox.workflow",
            {
                "kind": "inbox",
                "title": "Inbox",
                "prompt": "Use the message.",
                "agent_name": "progenitor",
                "inbox_message_id": "message-42",
            },
        ),
        (
            "node_routine.workflow",
            {
                "kind": "routine",
                "title": "Routine",
                "prompt": "Run the routine.",
                "agent_name": "progenitor",
                "routine_id": "daily-review",
                "routine_revision": 1,
            },
        ),
        (
            "node_condition.workflow",
            {
                "kind": "condition",
                "title": "Condition",
                "expression": "payload.card.priority == 'high'",
            },
        ),
        (
            "node_approval.workflow",
            {"kind": "approval", "title": "Approval", "message": "Approve this action."},
        ),
        (
            "node_mcp.workflow",
            {"kind": "mcp", "title": "MCP", "server_id": "github", "tool_name": "create_issue"},
        ),
    ],
)
def test_current_workflow_node_schemas_are_independently_loadable(schema_name, node):
    schema = load_schema(schema_name)
    definition_schema = load_schema("definition.workflow")
    definition_variant = next(
        variant
        for variant in definition_schema["properties"]["graph"]["properties"]["nodes"][
            "additionalProperties"
        ]["oneOf"]
        if variant["properties"]["kind"]["const"] == node["kind"]
    )

    assert {
        key: schema[key] for key in ("type", "additionalProperties", "required", "properties")
    } == definition_variant

    assert schema["properties"]["kind"]["const"] == node["kind"]
    assert validate_against_schema(node, schema) is None
