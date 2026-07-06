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
