"""Coverage test to ensure all system tools are registered.

System tools are implemented as BaseSystemTool subclasses under
app/progenitor/tools/*.py and must be discoverable/registered by
app/progenitor/nodes/node_helpers/registry.py.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType


def _iter_tool_modules() -> list[ModuleType]:
    import app.progenitor.tools as tools_pkg

    modules: list[ModuleType] = []
    for info in pkgutil.iter_modules(tools_pkg.__path__, tools_pkg.__name__ + "."):
        # Skip non-tool implementation modules and caches
        name = info.name.rsplit(".", 1)[-1]
        if name.startswith("__"):
            continue
        modules.append(importlib.import_module(info.name))
    return modules


def test_all_base_system_tools_are_registered():
    from app.progenitor.tools.protocol import BaseSystemTool
    from app.progenitor.nodes.node_helpers.registry import get_system_tool_names

    registered = set(get_system_tool_names())

    discovered: set[str] = set()
    for module in _iter_tool_modules():
        for obj in vars(module).values():
            if not isinstance(obj, type):
                continue
            if obj is BaseSystemTool:
                continue
            if not issubclass(obj, BaseSystemTool):
                continue

            try:
                instance = obj()
            except Exception:
                # If a tool requires constructor args it isn't a system tool.
                continue
            discovered.add(instance.name)

    missing = discovered - registered
    assert not missing, f"Unregistered system tools: {sorted(missing)}"
