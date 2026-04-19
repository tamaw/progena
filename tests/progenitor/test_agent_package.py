import importlib
import sys
import types


def test_agent_package_does_not_eagerly_import_deep_agent():
    sys.modules.pop("app.progenitor.agent.deep_agent", None)
    sys.modules.pop("app.progenitor.agent", None)

    module = importlib.import_module("app.progenitor.agent")

    assert module.__all__ == ["create_progenitor_agent"]
    assert "app.progenitor.agent.deep_agent" not in sys.modules


def test_stream_events_import_does_not_pull_in_deep_agent():
    sys.modules.pop("app.progenitor.agent.stream_events", None)
    sys.modules.pop("app.progenitor.agent.deep_agent", None)
    sys.modules.pop("app.progenitor.agent", None)

    importlib.import_module("app.progenitor.agent.stream_events")

    assert "app.progenitor.agent.deep_agent" not in sys.modules


def test_agent_package_resolves_create_progenitor_agent_lazily():
    sentinel = object()
    fake_module = types.ModuleType("app.progenitor.agent.deep_agent")
    fake_module.create_progenitor_agent = sentinel

    sys.modules.pop("app.progenitor.agent", None)
    sys.modules["app.progenitor.agent.deep_agent"] = fake_module

    module = importlib.import_module("app.progenitor.agent")

    assert module.create_progenitor_agent is sentinel
