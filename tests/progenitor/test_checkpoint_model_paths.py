"""Historical checkpoint type identities survive domain ownership changes."""

from importlib import import_module

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from src.progenitor.persistence.serialization import CheckpointSerializer
from src.progenitor.schedules.models import ScheduleEntry
from src.progenitor.sessions.models import ConversationMessage
from src.progenitor.workflows.documents import default_workflow_definition
from src.progenitor.workflows.models import WorkflowDefinition


@pytest.mark.parametrize("namespace", ["app", "src"])
@pytest.mark.parametrize(
    ("value", "old_module"),
    [
        (
            ScheduleEntry(id="schedule-fixture", message="Check compatibility"),
            "src.progenitor.state.models",
        ),
        (
            ConversationMessage(id="message-fixture", role="user", content="Existing conversation"),
            "src.progenitor.state.models",
        ),
        (
            WorkflowDefinition.model_validate(default_workflow_definition(workflow_id="fixture")),
            "src.progenitor.workflows",
        ),
    ],
)
def test_pre_refactor_checkpoint_reconstructs_current_model(
    value, old_module, namespace, monkeypatch
):
    old_module = old_module.replace("src.", namespace + ".", 1)
    serializer = CheckpointSerializer()
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


@pytest.mark.parametrize("model", [ScheduleEntry, ConversationMessage, WorkflowDefinition])
def test_pre_rename_domain_checkpoint_restores_current_type(model, monkeypatch):
    if model is WorkflowDefinition:
        value = model.model_validate(default_workflow_definition(workflow_id="fixture"))
    elif model is ConversationMessage:
        value = model(id="message-fixture", role="user", content="Keep app.progenitor.state.models")
    else:
        value = model(id="schedule-fixture", message="Keep app.progenitor.schedules.models")
    serializer = CheckpointSerializer(allowed_msgpack_modules=[model])
    current = serializer.loads_typed(serializer.dumps_typed(value))
    with monkeypatch.context() as patch:
        patch.setattr(model, "__module__", model.__module__.replace("src.", "app.", 1))
        encoded = serializer.dumps_typed({"model": value, "text": "app.progenitor.state.models"})
    restored = serializer.loads_typed(encoded)
    assert type(restored["model"]) is model
    assert restored["model"] == current
    assert restored["text"] == "app.progenitor.state.models"


def test_legacy_namespace_text_and_binary_content_remain_unchanged():
    value = {
        "text": "app.progenitor.state.models is a historical path",
        "bytes": b"app.progenitor.schedules.models",
        3: {"path": "/app/data", "module": "app.progenitor.workflows"},
    }
    serializer = CheckpointSerializer()
    assert serializer.loads_typed(JsonPlusSerializer().dumps_typed(value)) == value


def test_historical_model_aliases_resolve_only_to_current_contracts():
    state = import_module("src.progenitor.state.models")
    for name, module in state._MODEL_MODULES.items():
        assert getattr(state, name) is getattr(import_module(f"src.progenitor.{module}"), name)
    workflow = import_module("src.progenitor.workflows")
    contracts = import_module("src.progenitor.workflows.models")
    for name in workflow._CHECKPOINT_MODELS:
        assert getattr(workflow, name) is getattr(contracts, name)
    with pytest.raises(AttributeError):
        _ = workflow.default_workflow_definition
