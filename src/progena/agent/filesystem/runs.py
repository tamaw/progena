"""DeepAgent backend exposing routine, workflow and schedule runs under ``/runs/``.

Each top-level run is one read-only file::

    /runs/{routines,workflows,schedules}/<YYYY-MM-DD>_<slug>_<run_id>.md

rendered on read from data that already exists (routine lifecycle events and
checkpoints, ``workflow_run:`` documents, ``schedule_runs``), so nothing is
persisted and older runs need no backfill. Child runs (a workflow's routine
step, a routine's routine node) appear inside their parent. ``<file>#<n>``
returns step ``n``'s full value; it is never listed or searched.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
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
from src.progena.persistence.langgraph import CheckpointVersions
from src.progena.runs._render import (
    RoutineEventRevisions,
    RunContext,
    Steps,
)
from src.progena.runs.paths import RUN_DIRECTORIES, RunKind, run_file_name
from src.progena.runs.routine import TERMINAL_STATUSES, attempt_status, render_routine
from src.progena.runs.schedule import render_schedule, schedule_title
from src.progena.runs.workflow import render_workflow, workflow_title
from src.progena.runtime.instance import RUNTIME_INSTANCE_ID, RUNTIME_STARTED_AT
from src.progena.runtime.ownership import parse_timestamp, schedule_run_interrupted
from src.progena.workflows.documents import is_workflow_owned_routine_session

_READ_ONLY = "Runs are read-only."
_KINDS: dict[str, RunKind] = {directory: kind for kind, directory in RUN_DIRECTORIES.items()}
_FINISHED = TERMINAL_STATUSES | {"error", "interrupted"}
_CACHE_SIZE = 256

_FULL_RUN_FILE = re.compile(r"\d{4}-\d{2}-\d{2}_[a-z0-9-]+_([A-Za-z0-9_-]+)\.md")


@dataclass(frozen=True)
class _CachedRun:
    text: str
    steps: Steps
    checkpoint_threads: frozenset[str]
    checkpoint_versions: CheckpointVersions
    lifecycle_threads: frozenset[str]
    lifecycle_revisions: RoutineEventRevisions
    configurations: tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...], str | None]


class RunDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async, read-only backend rendering runs as IRC logs."""

    _ASYNC_ONLY = "RunDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any, checkpointer: Any, revision_reader: Any = None) -> None:
        self._sm = state_manager
        self._checkpointer = checkpointer
        self._revision_reader = revision_reader
        # Terminal renders are retained only while every causal checkpoint tip is unchanged.
        self._cache: OrderedDict[tuple[Any, ...], _CachedRun] = OrderedDict()

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
        payload = await self._read_payload(ctx, kind, name)
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

    async def _virtual_unlisted_file_exists(self, path: str) -> bool:
        """Resolve an old full-ID filename without exposing step references."""
        if "#" in path:
            return False
        directory, _, name = path.strip("/").removeprefix("runs").strip("/").partition("/")
        if _KINDS.get(directory) is None or _FULL_RUN_FILE.fullmatch(name) is None:
            return False
        ctx = await self._context()
        return await self._read_payload(ctx, _KINDS[directory], name) is not None

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
        preferences = preferences if isinstance(preferences, dict) else {}
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
            routine_events=[],
            _preferences=preferences,
        )

    async def _entries(self, ctx: RunContext, kind: RunKind) -> dict[str, dict[str, Any]]:
        """Compact directory entries, newest first with an identity tie-break."""
        rows: list[tuple[datetime, str, str, dict[str, Any]]] = []
        if kind == "routine":
            titles = await self._sm.list_routine_titles()
            for event in await self._sm.list_routine_run_summaries():
                if event.get("status") != "started" or _is_child(event):
                    continue
                routine_id = str(event.get("agent_id") or "routine")
                title = titles.get(routine_id) or routine_id
                started = _when(event.get("timestamp"))
                run_id = str(event.get("request_id") or "")
                rows.append((started, run_id, self._name(started, title, run_id, ctx), event))
        elif kind == "workflow":
            titles = await self._sm.list_workflow_titles()
            for run in await self._sm.list_workflow_run_summaries():
                workflow_id = str(run.get("workflow_id") or "")
                started = _when(run.get("started_at") or run.get("queued_at"))
                title = titles.get(workflow_id) or workflow_id or "workflow"
                run_id = str(run.get("run_id") or "")
                rows.append((started, run_id, self._name(started, title, run_id, ctx), run))
        else:
            schedules = await self._sm.get_schedule()
            for record in await self._sm.get_schedule_runs():
                data = record.model_dump(mode="json")
                started = _when(data.get("started_at") or data.get("created_at"))
                title = schedule_title(data, schedules)
                run_id = str(data.get("run_id") or "")
                rows.append(
                    (
                        started,
                        run_id,
                        self._name(started, title, run_id, ctx),
                        {**data, "_title": title},
                    )
                )
        rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return {name: payload for _, _, name, payload in rows}

    async def _read_payload(
        self, ctx: RunContext, kind: RunKind, name: str
    ) -> dict[str, Any] | None:
        """Resolve the full ID; filename date/title describe a run, not its identity."""
        match = _FULL_RUN_FILE.fullmatch(name)
        if match is None:
            return None
        run_id = match[1]
        if kind == "routine":
            payload = await self._sm.get_routine_run_started(run_id)
            if payload is None or _is_child(payload):
                return None
            thread_id = str(payload.get("thread_id") or "")
            await ctx.prepare_thread_events(thread_id)
            routine_id = str(payload.get("agent_id") or "routine")
            graph = await ctx.routine_graph(routine_id)
            title = str((graph or {}).get("name") or routine_id)
        elif kind == "workflow":
            payload = await self._sm.get_workflow_run(run_id)
            if payload is None:
                return None
            definition = await ctx.workflow_definition(str(payload.get("workflow_id") or ""))
            title = workflow_title(payload, definition)
        else:
            record = await self._sm.get_schedule_run(run_id)
            if record is None:
                return None
            payload = record.model_dump(mode="json")
            title = schedule_title(payload, await self._sm.get_schedule())
        return {**payload, "_title": title}

    @staticmethod
    def _name(started: datetime, title: str, run_id: Any, ctx: RunContext) -> str:
        return run_file_name(started_at=started, title=title, run_id=str(run_id or ""), tz=ctx.tz)

    # -- rendering -------------------------------------------------------------

    async def _render(
        self, ctx: RunContext, kind: RunKind, name: str, payload: dict[str, Any]
    ) -> tuple[str, Steps]:
        path = f"/runs/{RUN_DIRECTORIES[kind]}/{name}"
        status = self._status(ctx, kind, payload)
        boundaries = tuple(
            sorted(
                (str(event.get("request_id") or ""), str(event.get("timestamp") or ""))
                for event in ctx.routine_events
            )
        )
        key = (
            kind,
            name,
            payload.get("_title"),
            status,
            payload.get("updated_at"),
            boundaries,
            ctx.tz_name,
        )
        cached = self._cache.get(key) if status in _FINISHED else None
        if cached is not None:
            versions = await ctx.checkpoint_version_snapshot(cached.checkpoint_threads)
            lifecycle_revisions = await ctx.lifecycle_revision_snapshot(cached.lifecycle_threads)
            configurations_match = await ctx.configuration_matches(cached.configurations)
            if (
                versions == cached.checkpoint_versions
                and lifecycle_revisions == cached.lifecycle_revisions
                and configurations_match
            ):
                current = self._cache.get(key)
                if current is cached:
                    self._cache.move_to_end(key)
                    return cached.text, cached.steps
            elif self._cache.get(key) is cached:
                self._cache.pop(key)
        if kind == "routine":
            rendered = await render_routine(ctx, payload, path=path)
        elif kind == "workflow":
            rendered = await render_workflow(ctx, payload, path=path)
        else:
            record = {key: value for key, value in payload.items() if key != "_title"}
            rendered = await render_schedule(ctx, record, title=str(payload["_title"]), path=path)
        if status in _FINISHED:
            checkpoint_threads = frozenset(
                thread_id for thread_id, _ in ctx.checkpoint_dependencies
            )
            lifecycle_threads = frozenset(ctx.routine_event_versions)
            lifecycle_revisions_before = tuple(
                sorted(
                    revision
                    for revisions in ctx.routine_event_versions.values()
                    for revision in revisions
                )
            )
            configurations = ctx.configuration_snapshot()
            versions_after = await ctx.checkpoint_version_snapshot(checkpoint_threads, refresh=True)
            lifecycle_revisions_after = await ctx.lifecycle_revision_snapshot(
                lifecycle_threads, refresh=True
            )
            if (
                ctx.checkpoint_versions_before is not None
                and versions_after is not None
                and versions_after == ctx.checkpoint_versions_before
                and lifecycle_revisions_after == lifecycle_revisions_before
                and await ctx.configuration_matches(configurations, refresh=True)
            ):
                cached_run = _CachedRun(
                    text=rendered[0],
                    steps=rendered[1],
                    checkpoint_threads=checkpoint_threads,
                    checkpoint_versions=versions_after,
                    lifecycle_threads=lifecycle_threads,
                    lifecycle_revisions=lifecycle_revisions_after,
                    configurations=configurations,
                )
                if key not in self._cache:
                    self._cache[key] = cached_run
                    while len(self._cache) > _CACHE_SIZE:
                        self._cache.popitem(last=False)
        return rendered

    @staticmethod
    def _status(ctx: RunContext, kind: RunKind, payload: dict[str, Any]) -> str:
        if kind == "schedule":
            if schedule_run_interrupted(payload, ctx.live_instance, ctx.live_started_at):
                return "interrupted"
            return str(payload.get("status") or "queued").lower()
        if kind == "workflow":
            return str(payload.get("status") or "").lower()
        request_id = payload.get("request_id")
        events = [event for event in ctx.routine_events if event.get("request_id") == request_id]
        return attempt_status(ctx, events or [payload])[0]


def _is_child(event: dict[str, Any]) -> bool:
    """Runs started by a workflow step or a routine node live inside their parent."""
    if isinstance(event.get("parent"), dict) and event["parent"]:
        return True
    return is_workflow_owned_routine_session(str(event.get("session_id") or ""))


def _when(value: Any) -> datetime:
    return parse_timestamp(value) or datetime.fromtimestamp(0, UTC)
