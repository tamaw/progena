"""Agent Protocol execution of live object agents in either invocation mode."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from aiohttp import web
from langchain_core.messages import BaseMessage
from langgraph.types import Command

from src.progena.documents.values import make_json_safe
from src.progena.llm.structured import validate_against_schema
from src.progena.runtime.conversation_gate import ConversationTurnGate
from src.progena.sessions.context import reset_current_thread_id, set_current_thread_id
from src.progena.threads import object_agent_thread_id

from ..routines.messages import extract_json_object, normalize_message_content
from ..utils import utcnow_isoformat

THREAD_PREFIX = "agent_protocol_thread:"
RUN_PREFIX = "agent_protocol_run:"


@dataclass(frozen=True)
class ObjectAgentOwner:
    """Protocol identity retained by ACP tasks started in an object-agent run."""

    assistant_id: str
    thread_id: str
    run_id: str
    checkpoint_thread_id: str

    def as_metadata(self) -> dict[str, str]:
        return {
            "protocol": "object_agent",
            "assistant_id": self.assistant_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "checkpoint_thread_id": self.checkpoint_thread_id,
        }


_current_object_agent_owner: contextvars.ContextVar[ObjectAgentOwner | None] = (
    contextvars.ContextVar("progena_object_agent_owner", default=None)
)


def get_current_object_agent_owner() -> ObjectAgentOwner | None:
    """Return the object-agent protocol run currently executing, if any."""
    return _current_object_agent_owner.get()


def _owner_from_metadata(value: Any) -> ObjectAgentOwner | None:
    if not isinstance(value, dict) or value.get("protocol") != "object_agent":
        return None
    values = {
        key: str(value.get(key) or "").strip()
        for key in ("assistant_id", "thread_id", "run_id", "checkpoint_thread_id")
    }
    if not all(values.values()):
        return None
    return ObjectAgentOwner(**values)


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseMessage):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return make_json_safe(value)


class ObjectAgentProtocol:
    """Share execution between SDK background runs and wait-for-result calls.

    Postgres runtime documents retain thread/run history. The local task map
    contains only active execution handles, never authoritative run results.
    """

    def __init__(
        self,
        *,
        store: Any,
        resolve_agent: Callable[[str], Any | None],
        conversation_gate: ConversationTurnGate | None = None,
    ) -> None:
        self._store = store
        self._resolve_agent = resolve_agent
        self._conversation_gate = conversation_gate or ConversationTurnGate()
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def routes(self) -> list[web.RouteDef]:
        return [
            web.post("/agent-protocol/threads", self.create_thread),
            web.get("/agent-protocol/threads/{thread_id}", self.get_thread),
            web.post("/agent-protocol/threads/{thread_id}/runs", self.create_run),
            web.post("/agent-protocol/threads/{thread_id}/runs/wait", self.wait_run),
            web.get("/agent-protocol/threads/{thread_id}/runs/{run_id}", self.get_run),
            web.get("/agent-protocol/threads/{thread_id}/runs/{run_id}/join", self.join_run),
            web.post("/agent-protocol/threads/{thread_id}/runs/{run_id}/cancel", self.cancel_run),
        ]

    async def start(self) -> None:
        # A killed worker cannot be resumed safely by replaying arbitrary tools.
        # Retain its history and report interruption rather than leave it running.
        for key, run in await self._store.list_documents(RUN_PREFIX):
            if run.get("status") == "running":
                await self._store.save_document(
                    key,
                    {
                        **run,
                        "status": "error",
                        "error": "Runtime restarted during execution",
                        "updated_at": utcnow_isoformat(),
                    },
                )
                thread = await self._store.peek_document(THREAD_PREFIX + run["thread_id"])
                if thread is not None:
                    await self._store.save_document(
                        THREAD_PREFIX + run["thread_id"], {**thread, "status": "idle"}
                    )

    async def stop(self) -> None:
        for key in list(self._tasks):
            await self._cancel(*key)

    async def _cancel(self, thread_id: str, run_id: str) -> None:
        task = self._tasks.get((thread_id, run_id))
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # A task cancelled before its first instruction cannot run its finally.
        run = await self._run(thread_id, run_id)
        if run["status"] == "running":
            now = utcnow_isoformat()
            await self._store.save_document(
                f"{RUN_PREFIX}{thread_id}:{run_id}",
                {**run, "status": "cancelled", "updated_at": now},
            )
            thread = await self._thread(thread_id)
            await self._store.save_document(
                THREAD_PREFIX + thread_id, {**thread, "status": "idle", "updated_at": now}
            )
        self._tasks.pop((thread_id, run_id), None)

    async def _payload(self, request: web.Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise web.HTTPBadRequest(text="Invalid JSON") from exc
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Expected a JSON object")
        return payload

    async def _thread(self, thread_id: str) -> dict[str, Any]:
        thread = await self._store.peek_document(THREAD_PREFIX + thread_id)
        if thread is None:
            raise web.HTTPNotFound(text="Thread not found")
        return thread

    async def _run(self, thread_id: str, run_id: str) -> dict[str, Any]:
        run = await self._store.peek_document(f"{RUN_PREFIX}{thread_id}:{run_id}")
        if run is None:
            raise web.HTTPNotFound(text="Run not found")
        return run

    async def resume_acp_continuation(
        self,
        owner_metadata: Any,
        prompt: str,
        *,
        task_id: str,
    ) -> bool:
        """Start the owning object agent's next turn for one ACP material event.

        The task's persisted owner identity is verified against both protocol
        documents before creating a new run.  A live owner run is never
        interrupted by its callback; the heartbeat will retry that material
        event after the run becomes idle.
        """
        owner = _owner_from_metadata(owner_metadata)
        if (
            owner is None
            or not prompt.strip()
            or owner.checkpoint_thread_id
            != object_agent_thread_id(owner.assistant_id, owner.thread_id)
        ):
            return False
        runnable = self._resolve_agent(owner.assistant_id)
        if runnable is None:
            return False

        async with self._lock:
            try:
                thread = await self._thread(owner.thread_id)
                owner_run = await self._run(owner.thread_id, owner.run_id)
            except web.HTTPException:
                return False
            if (
                thread.get("assistant_id") != owner.assistant_id
                or owner_run.get("assistant_id") != owner.assistant_id
            ):
                return False
            if any(
                thread_id == owner.thread_id and not task.done()
                for (thread_id, _), task in self._tasks.items()
            ):
                return False

            now = utcnow_isoformat()
            run_id = uuid.uuid4().hex
            run = {
                "thread_id": owner.thread_id,
                "run_id": run_id,
                "assistant_id": owner.assistant_id,
                "model_profile": owner_run.get("model_profile"),
                "status": "running",
                "continuation_of": owner.run_id,
                "acp_task_id": task_id,
                "created_at": now,
                "updated_at": now,
            }
            await self._store.save_document(
                THREAD_PREFIX + owner.thread_id,
                {**thread, "status": "busy", "updated_at": now},
            )
            await self._store.save_document(f"{RUN_PREFIX}{owner.thread_id}:{run_id}", run)
            self._tasks[(owner.thread_id, run_id)] = asyncio.create_task(
                self._execute(
                    run,
                    runnable,
                    {"messages": [{"role": "user", "content": prompt}]},
                    None,
                )
            )
        return True

    async def create_thread(self, request: web.Request) -> web.Response:
        payload = await self._payload(request)
        thread_id = str(payload.get("thread_id") or uuid.uuid4().hex)
        async with self._lock:
            thread = await self._store.peek_document(THREAD_PREFIX + thread_id)
            if thread is None:
                now = utcnow_isoformat()
                thread = {
                    "thread_id": thread_id,
                    "values": {"messages": []},
                    "status": "idle",
                    "created_at": now,
                    "updated_at": now,
                }
                await self._store.save_document(THREAD_PREFIX + thread_id, thread)
        return web.json_response(thread)

    async def get_thread(self, request: web.Request) -> web.Response:
        return web.json_response(await self._thread(request.match_info["thread_id"]))

    async def _submit(self, request: web.Request) -> dict[str, Any]:
        payload = await self._payload(request)
        assistant_id = str(payload.get("assistant_id") or "")
        runnable = self._resolve_agent(assistant_id)
        if runnable is None:
            raise web.HTTPNotFound(text="Unknown object agent")
        if payload.get("input") is not None and payload.get("command") is not None:
            raise web.HTTPBadRequest(text="input and command are mutually exclusive")
        strategy = payload.get("multitask_strategy") or "reject"
        if strategy not in {"reject", "interrupt"}:
            raise web.HTTPBadRequest(text="Supported multitask strategies: reject, interrupt")
        command = payload.get("command")
        if command is not None:
            if not isinstance(command, dict) or set(command) != {"resume"}:
                raise web.HTTPBadRequest(text="Only approval resume commands are supported")
            invocation = Command(resume=command["resume"])
        else:
            invocation = payload.get("input") or {"messages": []}
            if not isinstance(invocation, dict):
                raise web.HTTPBadRequest(text="input must be a JSON object")
        thread_id = request.match_info["thread_id"]
        config = payload.get("config") or {}
        if not isinstance(config, dict):
            raise web.HTTPBadRequest(text="config must be an object")
        configurable = config.get("configurable") or {}
        if not isinstance(configurable, dict):
            raise web.HTTPBadRequest(text="configurable must be an object")
        model_profile = configurable.get("model_profile")
        if model_profile is not None and (
            not isinstance(model_profile, str) or not model_profile.strip()
        ):
            raise web.HTTPBadRequest(text="model_profile must be a non-empty name")
        response_schema = configurable.get("response_schema")
        if response_schema is not None and not isinstance(response_schema, dict):
            raise web.HTTPBadRequest(text="response_schema must be a JSON Schema object")
        async with self._lock:
            thread = await self._thread(thread_id)
            if thread.get("assistant_id") not in {None, assistant_id}:
                raise web.HTTPConflict(text="Thread belongs to another object agent")
            active = [
                (key, task)
                for key, task in self._tasks.items()
                if key[0] == thread_id and not task.done()
            ]
            if active and strategy == "reject":
                raise web.HTTPConflict(text="Thread already has an active run")
            for (active_thread, active_run), _ in active:
                await self._cancel(active_thread, active_run)
            now = utcnow_isoformat()
            run_id = uuid.uuid4().hex
            run = {
                "thread_id": thread_id,
                "run_id": run_id,
                "assistant_id": assistant_id,
                "model_profile": model_profile,
                "status": "running",
                "created_at": now,
                "updated_at": now,
            }
            await self._store.save_document(
                THREAD_PREFIX + thread_id,
                {**thread, "assistant_id": assistant_id, "status": "busy", "updated_at": now},
            )
            await self._store.save_document(f"{RUN_PREFIX}{thread_id}:{run_id}", run)
            self._tasks[(thread_id, run_id)] = asyncio.create_task(
                self._execute(run, runnable, invocation, response_schema)
            )
        return run

    async def create_run(self, request: web.Request) -> web.Response:
        return web.json_response(await self._submit(request))

    async def wait_run(self, request: web.Request) -> web.Response:
        run = await self._submit(request)
        return await self._result(run["thread_id"], run["run_id"])

    async def get_run(self, request: web.Request) -> web.Response:
        return web.json_response(
            await self._run(request.match_info["thread_id"], request.match_info["run_id"])
        )

    async def join_run(self, request: web.Request) -> web.Response:
        return await self._result(request.match_info["thread_id"], request.match_info["run_id"])

    async def _result(self, thread_id: str, run_id: str) -> web.Response:
        await self._run(thread_id, run_id)
        task = self._tasks.get((thread_id, run_id))
        if task is not None:
            await asyncio.shield(task)
        run = await self._run(thread_id, run_id)
        if run["status"] in {"error", "cancelled"}:
            return web.json_response(
                {
                    "__error__": {
                        "error": run["status"],
                        "message": run.get("error") or "Run cancelled",
                    }
                }
            )
        return web.json_response(run.get("values") or {})

    async def cancel_run(self, request: web.Request) -> web.Response:
        key = (request.match_info["thread_id"], request.match_info["run_id"])
        async with self._lock:
            await self._cancel(*key)
        return web.json_response({"ok": True})

    async def _execute(
        self,
        run: dict[str, Any],
        runnable: Any,
        invocation: Any,
        response_schema: dict[str, Any] | None,
    ) -> None:
        thread_id, run_id = run["thread_id"], run["run_id"]
        values: dict[str, Any] = {}
        error = None
        checkpoint_thread = object_agent_thread_id(run["assistant_id"], thread_id)
        owner_token = _current_object_agent_owner.set(
            ObjectAgentOwner(
                assistant_id=str(run["assistant_id"]),
                thread_id=thread_id,
                run_id=run_id,
                checkpoint_thread_id=checkpoint_thread,
            )
        )
        thread_token = set_current_thread_id(checkpoint_thread)
        try:

            async def invoke() -> Any:
                return await runnable.ainvoke(
                    invocation,
                    config={
                        "configurable": {
                            "thread_id": checkpoint_thread,
                            "object_agent_thread_id": checkpoint_thread,
                            "model_profile": run.get("model_profile"),
                        }
                    },
                )

            result = await self._conversation_gate.run(checkpoint_thread, invoke)
            values = _json_value(result)
            status = "interrupted" if values.get("__interrupt__") else "success"
            if response_schema is not None and status == "success":
                messages = result.get("messages") or []
                last = messages[-1] if messages else None
                content = (
                    last.content
                    if isinstance(last, BaseMessage)
                    else (last or {}).get("content", "")
                )
                response = extract_json_object(normalize_message_content(content))
                if response is None:
                    raise ValueError("Agent response was not a JSON object")
                validation_error = validate_against_schema(response, response_schema)
                if validation_error:
                    raise ValueError(f"Agent response failed schema validation: {validation_error}")
                values["response"] = response
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:
            status, error = "error", str(exc)
        finally:
            reset_current_thread_id(thread_token)
            _current_object_agent_owner.reset(owner_token)
        now = utcnow_isoformat()
        finished = {**run, "status": status, "error": error, "values": values, "updated_at": now}
        try:
            await self._store.save_document(f"{RUN_PREFIX}{thread_id}:{run_id}", finished)
            thread = await self._thread(thread_id)
            await self._store.save_document(
                THREAD_PREFIX + thread_id,
                {
                    **thread,
                    "values": values or thread["values"],
                    "status": "interrupted" if status == "interrupted" else "idle",
                    "updated_at": now,
                },
            )
        finally:
            self._tasks.pop((thread_id, run_id), None)
