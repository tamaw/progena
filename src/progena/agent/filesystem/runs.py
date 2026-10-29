"""DeepAgent backend exposing routine, workflow and schedule runs under ``/runs/``.

Each top-level run is one read-only file::

    /runs/{routines,workflows,schedules}/<YYYY-MM-DD>_<slug>_<id8>.md

rendered on read from data that already exists (routine lifecycle events and
checkpoints, ``workflow_run:`` documents, ``schedule_runs``), so nothing is
persisted and older runs need no backfill. Child runs (a workflow's routine
step, a routine's routine node) appear inside their parent. ``<file>#<n>``
returns step ``n``'s full value; it is never listed or searched.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from deepagents.backends.protocol import (
    EditResult,
    FileData,
    FileInfo,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import create_file_data, slice_read_response

from src.progena.agent.filesystem.base import _AsyncOnlyFileBackendMixin
from src.progena.runs._render import RunContext, Steps
from src.progena.runs.paths import RUN_DIRECTORIES, RunKind, run_file_name
from src.progena.runs.routine import TERMINAL_STATUSES, render_routine
from src.progena.runs.schedule import render_schedule, schedule_title
from src.progena.runs.workflow import render_workflow, workflow_title
from src.progena.runtime.instance import RUNTIME_INSTANCE_ID, RUNTIME_STARTED_AT
from src.progena.runtime.ownership import parse_timestamp

_READ_ONLY = "Runs are read-only."
_KINDS: dict[str, RunKind] = {directory: kind for kind, directory in RUN_DIRECTORIES.items()}
_FINISHED = TERMINAL_STATUSES | {"cancelled"}
_CACHE_SIZE = 256


class RunDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async, read-only backend rendering runs as IRC logs."""

    _ASYNC_ONLY = "RunDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any, checkpointer: Any, revision_reader: Any = None) -> None:
        self._sm = state_manager
        self._checkpointer = checkpointer
        self._revision_reader = revision_reader
        # Finished runs never change, so their rendering is cached exactly.
        self._cache: OrderedDict[tuple[str, str, str], tuple[str, Steps]] = OrderedDict()

    # -- filesystem surface --------------------------------------------------

    async def als(self, path: str) -> LsResult:
        directory = path.strip("/").removeprefix("runs").strip("/")
        if not directory:
            return LsResult(
                entries=[
                    FileInfo(path=f"/{name}/", is_dir=True) for name in RUN_DIRECTORIES.values()
                ]
            )
        kind = _KINDS.get(directory)
        if kind is None:
            return LsResult(error=f"Directory '{path}' not found")
        ctx = await self._context()
        entries = await self._entries(ctx, kind)
        return LsResult(
            entries=[FileInfo(path=f"/{directory}/{name}", is_dir=False) for name in entries]
        )

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        path, has_step, step = file_path.partition("#")
        directory, _, name = path.strip("/").removeprefix("runs").strip("/").partition("/")
        kind = _KINDS.get(directory)
        if kind is None or not name:
            return ReadResult(error=f"File '{file_path}' not found")
        ctx = await self._context()
        payload = (await self._entries(ctx, kind)).get(name)
        if payload is None:
            return ReadResult(error=f"File '{file_path}' not found")
        text, steps = await self._render(ctx, kind, name, payload)
        if has_step:
            value = steps.text(step)
            if value is None:
                return ReadResult(
                    error=f"Step '{step}' not found in '{path}' (it has {len(steps.values)})"
                )
            text = value
        data = create_file_data(text)
        sliced = slice_read_response(data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(file_data=FileData(content=sliced, encoding="utf-8"))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return EditResult(error=_READ_ONLY)

    async def adelete(self, file_path: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY)

    # -- listing ---------------------------------------------------------------

    async def _context(self) -> RunContext:
        preferences = await self._sm.get_preferences_config()
        tz_name = str(preferences.get("timezone") or "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            tz, tz_name = ZoneInfo("UTC"), "UTC"
        return RunContext(
            state_manager=self._sm,
            checkpointer=self._checkpointer,
            revision_reader=self._revision_reader,
            tz=tz,
            tz_name=tz_name,
            live_instance=RUNTIME_INSTANCE_ID,
            live_started_at=RUNTIME_STARTED_AT,
            routine_events=await self._sm.list_routine_run_events(),
        )

    async def _entries(self, ctx: RunContext, kind: RunKind) -> dict[str, dict[str, Any]]:
        """File name -> the run's payload, newest first."""
        rows: list[tuple[datetime, str, dict[str, Any]]] = []
        if kind == "routine":
            names: dict[str, str] = {}
            for event in ctx.routine_events:
                if event.get("status") != "started" or _is_child(event):
                    continue
                routine_id = str(event.get("agent_id") or "routine")
                if routine_id not in names:
                    graph = await self._sm.get_routine_graph(routine_id)
                    names[routine_id] = str((graph or {}).get("name") or routine_id)
                started = _when(event.get("timestamp"))
                rows.append(
                    (
                        started,
                        self._name(started, names[routine_id], event.get("request_id"), ctx),
                        event,
                    )
                )
        elif kind == "workflow":
            definitions: dict[str, Any] = {}
            for run in await self._sm.list_workflow_runs():
                workflow_id = str(run.get("workflow_id") or "")
                if workflow_id not in definitions:
                    definitions[workflow_id] = await self._sm.get_workflow_definition(workflow_id)
                started = _when(run.get("started_at") or run.get("queued_at"))
                title = workflow_title(run, definitions[workflow_id])
                rows.append((started, self._name(started, title, run.get("run_id"), ctx), run))
        else:
            schedules = await self._sm.get_schedule()
            for record in await self._sm.get_schedule_runs():
                data = (
                    record.model_dump(mode="json")
                    if hasattr(record, "model_dump")
                    else dict(record)
                )
                started = _when(data.get("started_at") or data.get("created_at"))
                title = schedule_title(data, schedules if isinstance(schedules, dict) else {})
                rows.append(
                    (
                        started,
                        self._name(started, title, data.get("run_id"), ctx),
                        {**data, "_title": title},
                    )
                )
        rows.sort(key=lambda row: row[0], reverse=True)
        return {name: payload for _, name, payload in rows}

    @staticmethod
    def _name(started: datetime, title: str, run_id: Any, ctx: RunContext) -> str:
        return run_file_name(started_at=started, title=title, run_id=str(run_id or ""), tz=ctx.tz)

    # -- rendering -------------------------------------------------------------

    async def _render(
        self, ctx: RunContext, kind: RunKind, name: str, payload: dict[str, Any]
    ) -> tuple[str, Steps]:
        path = f"/runs/{RUN_DIRECTORIES[kind]}/{name}"
        status = self._status(ctx, kind, payload)
        key = (kind, name, status)
        cached = self._cache.get(key) if status in _FINISHED else None
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        if kind == "routine":
            rendered = await render_routine(ctx, payload, path=path)
        elif kind == "workflow":
            rendered = await render_workflow(ctx, payload, path=path)
        else:
            record = {k: v for k, v in payload.items() if k != "_title"}
            rendered = await render_schedule(ctx, record, title=str(payload["_title"]), path=path)
        if status in _FINISHED:
            self._cache[key] = rendered
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
        return rendered

    @staticmethod
    def _status(ctx: RunContext, kind: RunKind, payload: dict[str, Any]) -> str:
        if kind != "routine":
            return str(payload.get("status") or "")
        request_id = payload.get("request_id")
        events = [e for e in ctx.routine_events if e.get("request_id") == request_id]
        latest = max(events, key=lambda e: str(e.get("timestamp") or ""), default=payload)
        return str(latest.get("status") or "")


def _is_child(event: dict[str, Any]) -> bool:
    """Runs started by a workflow step or a routine node live inside their parent."""
    if isinstance(event.get("parent"), dict) and event["parent"]:
        return True
    return str(event.get("session_id") or "").startswith("workflow_run_")


def _when(value: Any) -> datetime:
    return parse_timestamp(value) or datetime.fromtimestamp(0, UTC)
