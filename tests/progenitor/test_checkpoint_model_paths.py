"""Historical checkpoint type identities survive domain ownership changes."""

from importlib import import_module

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.progenitor.schedules.models import ScheduleEntry
from app.progenitor.sessions.models import ConversationMessage
from app.progenitor.workflows.documents import default_workflow_definition
from app.progenitor.workflows.models import WorkflowDefinition


@pytest.mark.parametrize(
    ("value", "old_module"),
    [
        (
            ScheduleEntry(id="schedule-fixture", message="Check compatibility"),
            "app.progenitor.state.models",
        ),
        (
            ConversationMessage(id="message-fixture", role="user", content="Existing conversation"),
            "app.progenitor.state.models",
        ),
        (
            WorkflowDefinition.model_validate(default_workflow_definition(workflow_id="fixture")),
            "app.progenitor.workflows",
        ),
    ],
)
def test_pre_refactor_checkpoint_reconstructs_current_model(value, old_module, monkeypatch):
    serializer = JsonPlusSerializer()
    model = type(value)
    current_round_trip = serializer.loads_typed(serializer.dumps_typed(value))
    # Encode the historical identity; restore the current identity before load.
    with monkeypatch.context() as patch:
        patch.setattr(model, "__module__", old_module)
        encoded = serializer.dumps_typed(value)
    assert old_module.encode() in encoded[1]

    restored = serializer.loads_typed(encoded)
    assert type(restored) is model
    assert restored == current_round_trip


def test_historical_model_aliases_resolve_only_to_current_contracts():
    state = import_module("app.progenitor.state.models")
    for name, module in state._MODEL_MODULES.items():
        assert getattr(state, name) is getattr(import_module(f"app.progenitor.{module}"), name)
    workflow = import_module("app.progenitor.workflows")
    contracts = import_module("app.progenitor.workflows.models")
    for name in workflow._CHECKPOINT_MODELS:
        assert getattr(workflow, name) is getattr(contracts, name)
    with pytest.raises(AttributeError):
        _ = workflow.default_workflow_definition
