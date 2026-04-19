import importlib
import sys


def _clear_imports() -> None:
    for name in [
        "app.progenitor.agent",
        "app.progenitor.agent.deep_agent",
        "app.progenitor.runtime.loop",
        "app.progenitor.runtime.deep_agent_adapter",
        "app.progenitor.service",
        "app.progenitor.stream_events",
    ]:
        sys.modules.pop(name, None)


def test_stream_events_import_does_not_pull_in_agent_package():
    _clear_imports()

    importlib.import_module("app.progenitor.stream_events")

    assert "app.progenitor.agent" not in sys.modules
    assert "app.progenitor.agent.deep_agent" not in sys.modules


def test_runtime_loop_import_does_not_pull_in_agent_deep_agent():
    _clear_imports()

    importlib.import_module("app.progenitor.runtime.loop")

    assert "app.progenitor.agent.deep_agent" not in sys.modules


def test_service_import_does_not_pull_in_agent_deep_agent():
    _clear_imports()

    importlib.import_module("app.progenitor.service")

    assert "app.progenitor.agent.deep_agent" not in sys.modules
