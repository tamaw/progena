"""Integration regressions for searching stable /runs/ filenames."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend
from langgraph.store.memory import InMemoryStore

from src.progena.agent.filesystem.mounts import ReadOnlyBackend, create_progena_backend
from src.progena.agent.filesystem.runs import RunDirectoryBackend
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.state.manager import StateManager

_NS = ("progena", "state")


class _Checkpoints:
    async def aget_tuple(self, config: dict[str, Any]) -> None:
        return None

    async def alist(self, config: dict[str, Any]):
        return
        yield


async def test_composite_searches_an_old_workflow_full_id_path_after_rename_and_rollover() -> None:
    store = InMemoryStore()
    state_manager = StateManager(LangGraphRuntimeStore(store))
    backend = create_progena_backend(state_manager=state_manager, checkpointer=_Checkpoints())
    workflow_id = "workflow_exact"
    run_id = "workflow_run_exact"
    old_path = f"/runs/workflows/2026-10-28_needle-workflow_{run_id}.md"

    await state_manager.save_workflow_definition(
        workflow_id,
        {"id": workflow_id, "title": "Needle workflow", "graph": {"nodes": {}}},
    )
    await store.aput(
        _NS,
        f"workflow_run:{run_id}",
        {
            "payload": {
                "run_id": run_id,
                "workflow_id": workflow_id,
                "status": "completed",
                "source": "manual",
                "queued_at": "2026-10-28T23:30:00+00:00",
                "completed_at": "2026-10-28T23:31:00+00:00",
                "trigger_event": {"topic": "needle saved content"},
            }
        },
    )

    initial_listing = await backend.als("/runs/workflows/")
    assert old_path in [entry["path"] for entry in initial_listing.entries or []]

    await state_manager.save_workflow_definition(
        workflow_id,
        {"id": workflow_id, "title": "Renamed workflow", "graph": {"nodes": {}}},
    )
    preferences = await state_manager.get_preferences_config()
    await state_manager.save_preferences_config({**preferences, "timezone": "Asia/Tokyo"})

    listing = await backend.als("/runs/workflows/")
    assert old_path not in [entry["path"] for entry in listing.entries or []]

    read = await backend.aread(old_path, 0, 10_000)
    assert read.error is None
    assert "needle saved content" in read.file_data["content"]
    assert [match["path"] for match in (await backend.aglob("*.md", path=old_path)).matches] == [
        old_path
    ]

    grep = await backend.agrep("needle saved content", path=old_path)
    assert [match["path"] for match in grep.matches or []] == [old_path]
    assert "needle saved content" in (grep.matches or [])[0]["text"]

    step_path = f"{old_path}#1"
    assert not (await backend.als(step_path)).entries
    assert (await backend.agrep("needle saved content", path=step_path)).matches == []
    assert (await backend.aglob("*.md", path=step_path)).matches == []
    short_path = old_path.replace(run_id, "exact")
    assert (await backend.aread(short_path, 0, 10_000)).error is not None
    assert (await backend.aglob("*.md", path=short_path)).matches == []


async def test_public_read_only_search_bounds_hydration(tmp_path: Path) -> None:
    store = InMemoryStore()
    manager = StateManager(LangGraphRuntimeStore(store))
    await manager.save_preferences_config({"timezone": "UTC"})
    await manager.save_workflow_definition(
        "search", {"id": "search", "title": "Search", "graph": {"nodes": {}}}
    )
    for index in range(40):
        run_id = f"workflow_run_search{index:03d}"
        await store.aput(
            _NS,
            f"workflow_run:{run_id}",
            {
                "payload": {
                    "run_id": run_id,
                    "workflow_id": "search",
                    "status": "failed",
                    "started_at": "2026-10-30T10:00:00Z",
                    "last_error": "needle",
                }
            },
        )
    runs = RunDirectoryBackend(manager, _Checkpoints())
    reads: list[str] = []
    original_read = runs.aread

    async def counted_read(file_path: str, offset: int = 0, limit: int = 2000):
        reads.append(file_path)
        return await original_read(file_path, offset, limit)

    runs.aread = counted_read
    mounted = CompositeBackend(
        default=FilesystemBackend(root_dir=str(tmp_path)),
        routes={"/runs/": ReadOnlyBackend(runs)},
    )
    result = await mounted.agrep("needle", path="/runs/workflows/", max_count=1)
    assert len(result.matches or []) == 1
    assert result.truncated
    assert len(reads) == 1
