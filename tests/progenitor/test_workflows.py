import pytest

from app.progenitor.workflows import (
    default_workflow_definition,
    validate_workflow_definition_payload,
)


def test_workflow_definition_validator_accepts_minimal_event_prompt_chain():
    payload = default_workflow_definition(title="Validator check")

    normalized = validate_workflow_definition_payload(payload)

    assert normalized["title"] == "Validator check"
    assert normalized["trigger"]["event_type"] == "kanban.card.created"


def test_workflow_definition_validator_rejects_unknown_edge_target():
    payload = default_workflow_definition()
    payload["graph"]["edges"][0]["to"]["node"] = "missing-node"

    with pytest.raises(ValueError, match="unknown target node"):
        validate_workflow_definition_payload(payload)


def test_workflow_definition_validator_accepts_user_and_code_nodes():
    payload = default_workflow_definition()
    event_id, prompt_id = list(payload["graph"]["nodes"])
    user_id = "user_step"
    code_id = "code_step"
    payload["graph"]["nodes"][user_id] = {
        "kind": "user",
        "title": "Notify",
        "message_type": "notify_user",
        "message": "The workflow finished.",
        "notification_title": "Workflow done",
        "level": "success",
    }
    payload["graph"]["nodes"][code_id] = {
        "kind": "code",
        "title": "Summarize",
        "language": "python",
        "content": "print('hello')",
    }
    payload["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "default"},
            "to": {"node": prompt_id, "port": "default"},
        },
        {
            "from": {"node": prompt_id, "port": "default"},
            "to": {"node": user_id, "port": "default"},
        },
        {"from": {"node": user_id, "port": "default"}, "to": {"node": code_id, "port": "default"}},
    ]

    normalized = validate_workflow_definition_payload(payload)

    assert normalized["graph"]["nodes"][user_id]["message_type"] == "notify_user"
    assert normalized["graph"]["nodes"][code_id]["language"] == "python"


def test_workflow_definition_validator_rejects_notify_user_without_title():
    payload = default_workflow_definition()
    event_id, prompt_id = list(payload["graph"]["nodes"])
    user_id = "user_step"
    payload["graph"]["nodes"][user_id] = {
        "kind": "user",
        "title": "Notify",
        "message_type": "notify_user",
        "message": "The workflow finished.",
        "notification_title": None,
        "level": "info",
    }
    payload["graph"]["edges"].append(
        {"from": {"node": prompt_id, "port": "default"}, "to": {"node": user_id, "port": "default"}}
    )

    with pytest.raises(ValueError, match="notification_title"):
        validate_workflow_definition_payload(payload)


def test_workflow_definition_validator_rejects_empty_code_content():
    payload = default_workflow_definition()
    event_id, prompt_id = list(payload["graph"]["nodes"])
    code_id = "code_step"
    payload["graph"]["nodes"][code_id] = {
        "kind": "code",
        "title": "Summarize",
        "language": "python",
        "content": "   ",
    }
    payload["graph"]["edges"].append(
        {"from": {"node": prompt_id, "port": "default"}, "to": {"node": code_id, "port": "default"}}
    )

    with pytest.raises(ValueError, match="missing content"):
        validate_workflow_definition_payload(payload)


def test_workflow_definition_validator_accepts_column_filtered_moved_trigger():
    payload = default_workflow_definition(event_type="kanban.card.moved")
    payload["trigger"]["from_column"] = "backlog"
    payload["trigger"]["to_column"] = "plan"

    normalized = validate_workflow_definition_payload(payload)

    assert normalized["trigger"]["event_type"] == "kanban.card.moved"
    assert normalized["trigger"]["from_column"] == "backlog"
    assert normalized["trigger"]["to_column"] == "plan"


def test_workflow_definition_validator_rejects_unknown_column():
    payload = default_workflow_definition(event_type="kanban.card.moved")
    payload["trigger"]["to_column"] = "not-a-real-column"

    with pytest.raises(ValueError, match="schema validation failed"):
        validate_workflow_definition_payload(payload)


def test_workflow_definition_validator_rejects_cycles():
    payload = default_workflow_definition()
    _event_id, prompt_a_id = list(payload["graph"]["nodes"])
    prompt_b_id = "prompt_cycle"
    payload["graph"]["nodes"][prompt_b_id] = {
        "kind": "prompt",
        "title": "Cycle prompt",
        "prompt": "Create a cycle",
        "agent_name": "progenitor",
        "role": None,
    }
    payload["graph"]["edges"].append(
        {
            "from": {"node": prompt_a_id, "port": "default"},
            "to": {"node": prompt_b_id, "port": "default"},
        }
    )
    payload["graph"]["edges"].append(
        {
            "from": {"node": prompt_b_id, "port": "default"},
            "to": {"node": prompt_a_id, "port": "default"},
        }
    )

    with pytest.raises(ValueError, match="acyclic"):
        validate_workflow_definition_payload(payload)
