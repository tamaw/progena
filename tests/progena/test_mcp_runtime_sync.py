"""Tests for live MCP re-sync plumbing: the coalescing scheduler, the
disabled-flag re-apply, the runtime/backend NOTIFY channel match, and the
``mcp`` status tool reporting only enabled tools.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.progena.orchestration.nodes.mcp import reapply_mcp_disabled_tools
from src.progena.runtime.mcp_sync import MCPSyncScheduler
from src.progena.tools.mcp import MCPTool
from src.progena.tools.protocol import ToolContext

# --------------------------------------------------------------------------
# MCPSyncScheduler: single-flight + coalescing
# --------------------------------------------------------------------------


async def test_scheduler_runs_reapply_only():
    calls: list[str] = []

    async def full() -> None:
        calls.append("full")

    async def reapply() -> None:
        calls.append("reapply")

    sched = MCPSyncScheduler(full_resync=full, reapply=reapply)
    sched.request(full=False)
    await asyncio.sleep(0.02)
    await sched.aclose()
    assert calls == ["reapply"]


async def test_scheduler_coalesces_burst_into_single_extra_run():
    proceed = asyncio.Event()
    started = asyncio.Event()
    calls: list[str] = []

    async def full() -> None:
        calls.append("full")
        started.set()
        await proceed.wait()

    async def reapply() -> None:
        calls.append("reapply")

    sched = MCPSyncScheduler(full_resync=full, reapply=reapply)
    sched.request(full=True)
    await started.wait()  # first run is in-flight, blocked
    # A burst of requests while one is running must collapse to a single re-run.
    for _ in range(5):
        sched.request(full=True)
    proceed.set()
    await asyncio.sleep(0.02)
    await sched.aclose()
    assert calls.count("full") == 2  # 1 in-flight + 1 coalesced, not 6


async def test_full_resync_subsumes_pending_reapply():
    proceed = asyncio.Event()
    started = asyncio.Event()
    calls: list[str] = []

    async def full() -> None:
        calls.append("full")
        started.set()
        await proceed.wait()

    async def reapply() -> None:
        calls.append("reapply")

    sched = MCPSyncScheduler(full_resync=full, reapply=reapply)
    sched.request(full=True)
    await started.wait()
    sched.request(full=False)  # reapply pending...
    sched.request(full=True)  # ...subsumed by a full resync
    proceed.set()
    await asyncio.sleep(0.02)
    await sched.aclose()
    assert calls == ["full", "full"]  # reapply never runs on its own


async def test_scheduler_survives_work_exception():
    calls: list[str] = []

    async def full() -> None:
        calls.append("full")
        raise RuntimeError("boom")

    async def reapply() -> None:
        calls.append("reapply")

    sched = MCPSyncScheduler(full_resync=full, reapply=reapply)
    sched.request(full=True)
    await asyncio.sleep(0.02)
    sched.request(full=False)  # still works after a prior failure
    await asyncio.sleep(0.02)
    await sched.aclose()
    assert calls == ["full", "reapply"]


# --------------------------------------------------------------------------
# reapply_mcp_disabled_tools
# --------------------------------------------------------------------------


async def test_reapply_disabled_tools_from_doc():
    runtime = SimpleNamespace(disabled=None)
    runtime.set_disabled_tools = lambda names: setattr(runtime, "disabled", set(names))
    llm = SimpleNamespace(mcp_runtime=runtime)

    class SM:
        async def get_mcp_tools(self):
            return {
                "tools": [
                    {"name": "a", "enabled": True},
                    {"name": "b", "enabled": False},
                    {"name": "c", "enabled": False},
                ]
            }

    count, changed = await reapply_mcp_disabled_tools(llm, SM())
    assert count == 2
    assert changed is True
    assert runtime.disabled == {"b", "c"}


async def test_reapply_noops_without_runtime():
    count, changed = await reapply_mcp_disabled_tools(SimpleNamespace(mcp_runtime=None), object())
    assert count == 0
    assert changed is False


# --------------------------------------------------------------------------
# mcp status tool: report only the live (enabled) tool set
# --------------------------------------------------------------------------


async def test_mcp_tool_reports_enabled_only():
    runtime = SimpleNamespace(
        mcp_tools=[
            {"name": "s_a", "mcp_server_name": "s", "description": "A"},
            {"name": "s_b", "mcp_server_name": "s", "description": "B"},
        ],
        mcp_servers={"s": {}},
        disabled_tools={"s_b"},
    )
    llm = SimpleNamespace(mcp_runtime=runtime, mcp_config=None)
    ctx = ToolContext(tool_registry=None, llm_client=llm)

    result = await MCPTool().execute(ctx)
    assert result.success
    assert result.data["tool_count"] == 1
    assert result.data["disabled_count"] == 1
    assert [t["name"] for t in result.data["tools"]] == ["s_a"]
