import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager

_MODULE_NAMES = [
    "app.progenitor.agent.agent",
    "app.progenitor.runtime.heartbeat",
    "app.progenitor.agent.adapters",
    "app.progenitor.ipc.stream_events",
]


@contextmanager
def _isolated_imports() -> Iterator[None]:
    saved_modules = {name: sys.modules.get(name) for name in _MODULE_NAMES}
    for name in _MODULE_NAMES:
        sys.modules.pop(name, None)

    try:
        yield
    finally:
        for name in _MODULE_NAMES:
            sys.modules.pop(name, None)
        for name, module in saved_modules.items():
            if module is not None:
                sys.modules[name] = module


def test_stream_events_import_does_not_pull_in_agent_package():
    with _isolated_imports():
        importlib.import_module("app.progenitor.ipc.stream_events")

        assert "app.progenitor.agent.agent" not in sys.modules


def test_runtime_loop_import_does_not_pull_in_agent_setup():
    with _isolated_imports():
        importlib.import_module("app.progenitor.runtime.heartbeat")

        assert "app.progenitor.agent.agent" not in sys.modules
