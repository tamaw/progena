"""Greetings Agent — minimal standalone A2A test agent.

A deliberately tiny, fully isolated A2A server used to exercise Progenitor's
A2A tooling end-to-end (register, call_agent, heartbeat polling, push
callbacks). It wraps a deepagents deep agent that replies with a friendly
greeting and has two tools:

- ``sleep`` — pretend work for N seconds, to exercise async tracked invocation.
- ``probe_copilot`` — spawn the GitHub Copilot CLI over ACP, ask it to say
  hello back, and relay the reply.

Everything lives in this file and in memory. It imports nothing from
``app.progenitor`` or other ``app.agents.*`` packages — only third-party
libraries. Set ``PROGENITOR_MOCK_LLM=true`` for a deterministic no-LLM mode
(used by the unit tests): canned greetings, real sleeps for "sleep N", a
forced error for requests containing "fail".

Note on blocking semantics: the executor returns from ``execute()``
immediately after emitting ``submitted`` and completes the task from a
background worker via direct task-store writes, because the a2a-sdk closes
the event queue as soon as ``execute()`` returns. A ``blocking=true``
``message/send`` therefore still returns the submitted snapshot; callers are
expected to poll ``tasks/get`` (Progenitor always does).

Run:  python -m uvicorn app.agents.greetings_agent.agent:app --port 8109
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    PushNotificationSender,
    TaskStore,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from acp import PROTOCOL_VERSION, Client, spawn_agent_process, text_block
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    CreateTerminalResponse,
    DeniedOutcome,
    EnvVariable,
    KillTerminalCommandResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    ToolCallUpdate,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
from fastapi.responses import JSONResponse
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

logger = logging.getLogger("greetings_agent")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENT_ID = "greetings_agent"
AGENT_DESCRIPTION = (
    "Minimal A2A test agent: replies with a friendly greeting, can pretend to "
    "work (sleep) before responding, and can probe the local GitHub Copilot "
    "CLI over ACP to have it say hello back."
)
AGENT_VERSION = "0.1.0"
DEFAULT_PORT = 8109

_PORT = int(os.environ.get("PORT", str(DEFAULT_PORT)))
_HOST_LABEL = os.environ.get("A2A_PUBLIC_HOST", "localhost").strip() or "localhost"
_BIND_HOST = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
GREETINGS_SLEEP_MAX = float(os.environ.get("GREETINGS_SLEEP_MAX", "60"))
GREETINGS_COPILOT_TIMEOUT = float(os.environ.get("GREETINGS_COPILOT_TIMEOUT", "120"))

_TERMINAL_STATES = frozenset(
    {TaskState.completed, TaskState.failed, TaskState.canceled, TaskState.rejected}
)


def _is_mock() -> bool:
    """Read per request so tests can toggle via monkeypatch without reimport."""
    return os.environ.get("PROGENITOR_MOCK_LLM", "").strip().lower() in {"true", "1", "yes"}


def _model_name() -> str:
    return (
        os.environ.get("AGENT_MODEL_DEFAULT", "").strip()
        or os.environ.get("PROGENITOR_MODEL_DEFAULT", "").strip()
        or "gpt-5-mini"
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _text_message(text: str) -> Message:
    return Message(
        message_id=str(uuid.uuid4()),
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
    )


def _request_text(message: Message | None) -> str:
    if message is None:
        return ""
    parts = getattr(message, "parts", None) or []
    return "".join(
        getattr(getattr(part, "root", part), "text", "")
        for part in parts
        if isinstance(getattr(getattr(part, "root", part), "text", None), str)
    ).strip()


# ---------------------------------------------------------------------------
# Copilot ACP probe
# ---------------------------------------------------------------------------


class _ProbeACPClient(Client):
    """Collects Copilot's streamed reply text; auto-approves permission requests.

    ``acp.Client`` is abstract, so every callback must be implemented; the
    terminal/file stubs below are benign no-ops (a hello probe never uses them).
    """

    def __init__(self) -> None:
        self.text_parts: list[str] = []

    def on_connect(self, conn: Any) -> None:
        _ = conn

    async def session_update(self, session_id: str, update: Any, **_kwargs: Any) -> None:
        _ = session_id
        if isinstance(update, AgentMessageChunk):
            text = getattr(getattr(update, "content", None), "text", None)
            if text:
                self.text_parts.append(text)

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **_kwargs: Any,
    ) -> RequestPermissionResponse:
        _ = (session_id, tool_call)
        if options and hasattr(options[0], "id"):
            return RequestPermissionResponse(
                outcome=AllowedOutcome(option_id=options[0].id, outcome="selected")
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
        **_kwargs: Any,
    ) -> CreateTerminalResponse:
        _ = (command, session_id, args, cwd, env, output_byte_limit)
        return CreateTerminalResponse(terminal_id="internal")

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **_kwargs: Any,
    ) -> ReadTextFileResponse:
        _ = (path, session_id, limit, line)
        return ReadTextFileResponse(content="")

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **_kwargs: Any,
    ) -> WriteTextFileResponse | None:
        _ = (content, path, session_id)
        return None

    async def terminal_output(
        self, session_id: str, terminal_id: str, **_kwargs: Any
    ) -> TerminalOutputResponse:
        _ = (session_id, terminal_id)
        return TerminalOutputResponse(output="", truncated=False)

    async def release_terminal(
        self, session_id: str, terminal_id: str, **_kwargs: Any
    ) -> ReleaseTerminalResponse | None:
        _ = (session_id, terminal_id)
        return None

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **_kwargs: Any
    ) -> KillTerminalCommandResponse | None:
        _ = (session_id, terminal_id)
        return None

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **_kwargs: Any
    ) -> WaitForTerminalExitResponse:
        _ = (session_id, terminal_id)
        return WaitForTerminalExitResponse(exit_code=0)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        _ = (method, params)
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        _ = (method, params)

    def get_output(self) -> str:
        return "".join(self.text_parts)


async def _probe_copilot(message: str) -> str:
    """Spawn ``copilot --acp`` in a throwaway sandbox, send one prompt, return its reply."""
    command = os.environ.get("PROGENITOR_COPILOT_BIN", "copilot").strip() or "copilot"
    args = ["--acp", "--yolo"]
    model = os.environ.get("DEFAULT_COPILOT_MODEL", "gpt-5-mini").strip()
    if model:
        args.extend(["--model", model])

    sandbox = tempfile.mkdtemp(prefix="greetings_probe_")
    client = _ProbeACPClient()
    logger.info("Probing Copilot over ACP: %s %s", command, " ".join(args))
    try:
        async with spawn_agent_process(
            client, command, *args, cwd=sandbox, env=dict(os.environ)
        ) as (conn, _process):
            await conn.initialize(protocol_version=PROTOCOL_VERSION)
            session = await conn.new_session(cwd=sandbox, mcp_servers=[])
            await asyncio.wait_for(
                conn.prompt(prompt=[text_block(message)], session_id=session.session_id),
                timeout=GREETINGS_COPILOT_TIMEOUT,
            )
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    return client.get_output().strip() or "(Copilot returned no text)"


# ---------------------------------------------------------------------------
# LLM + deep agent
# ---------------------------------------------------------------------------


def _normalize_base_url(raw: str) -> str:
    """Strip a trailing ``/responses`` (and any query) so ChatOpenAI gets a clean base URL."""
    raw = (raw or "").strip()
    if not raw:
        return raw
    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/responses"):
        path = path[: -len("/responses")]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _create_model() -> ChatOpenAI:
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    base_url = _normalize_base_url(os.environ.get("AZURE_OPENAI_BASE_URL", ""))
    return ChatOpenAI(
        model=_model_name(),
        base_url=base_url or None,
        api_key=SecretStr(api_key or "not-needed"),
        default_headers={"api-key": api_key},
        use_responses_api=True,
        output_version="responses/v1",
        streaming=True,
    )


@tool
async def sleep(seconds: float) -> str:
    """Pretend to do slow work: pause for the given number of seconds before replying."""
    duration = max(0.0, min(float(seconds), GREETINGS_SLEEP_MAX))
    await asyncio.sleep(duration)
    return f"Slept {duration:.1f}s."


@tool
async def probe_copilot(message: str) -> str:
    """Ask the local GitHub Copilot CLI (over ACP) to respond to a message; returns its reply."""
    return await _probe_copilot(message)


_SYSTEM_PROMPT = """\
You are greetings_agent, a tiny test agent. Your only job is to reply with a short,
friendly greeting (1-2 sentences).

- If the request asks you to sleep, wait, or take your time for some number of seconds,
  call the `sleep` tool with that duration first, then greet.
- If the request mentions Copilot, call the `probe_copilot` tool to ask the local GitHub
  Copilot CLI to say hello back, and include its reply in your response.
- Otherwise just greet the caller warmly. Keep replies brief.
"""

_deep_agent: Any | None = None
_deep_agent_lock = asyncio.Lock()


async def _get_deep_agent() -> Any:
    global _deep_agent
    if _deep_agent is None:
        async with _deep_agent_lock:
            if _deep_agent is None:
                # Imported lazily: deepagents pulls in heavy optional deps, and
                # mock mode / card tests never need it.
                from deepagents import create_deep_agent

                _deep_agent = create_deep_agent(
                    model=_create_model(),
                    tools=[sleep, probe_copilot],
                    system_prompt=_SYSTEM_PROMPT,
                    name=AGENT_ID,
                )
    return _deep_agent


def _extract_text(result: dict[str, Any]) -> str:
    """Extract the final user-facing text from a deep agent result."""
    messages = result.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            content = getattr(msg, "content", None) or (
                msg.get("content") if isinstance(msg, dict) else None
            )
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        parts.append(str(part.get("text") or part.get("value") or ""))
                text = "".join(parts).strip()
                if text:
                    return text
    return "Hello! (greetings_agent produced no text)"


def _extract_tool_calls(result: dict[str, Any]) -> list[str]:
    tool_calls: list[str] = []
    messages = result.get("messages")
    if not isinstance(messages, list):
        return tool_calls
    for msg in messages:
        raw_calls = getattr(msg, "tool_calls", None) or (
            msg.get("tool_calls") if isinstance(msg, dict) else None
        )
        if not isinstance(raw_calls, list):
            continue
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "").strip()
            if name:
                tool_calls.append(name)
    return tool_calls


_SLEEP_RE = re.compile(r"sleep\s+(\d+(?:\.\d+)?)", re.IGNORECASE)


async def _mock_response(request_text: str) -> str:
    """Deterministic handler for PROGENITOR_MOCK_LLM mode (no API key needed)."""
    lowered = request_text.lower()
    match = _SLEEP_RE.search(request_text)
    if match:
        duration = min(float(match.group(1)), GREETINGS_SLEEP_MAX)
        await asyncio.sleep(duration)
        return f"Hello! I pretended to work for {duration:g} seconds."
    if "fail" in lowered:
        raise RuntimeError("Intentional failure requested by mock input")
    if "copilot" in lowered:
        reply = await _probe_copilot("Say hello back in one short sentence.")
        return f"Copilot says: {reply}"
    return f"Hello from greetings_agent! You said: {request_text!r}"


async def _run_greeting(request_text: str) -> str:
    if _is_mock():
        return await _mock_response(request_text)
    agent = await _get_deep_agent()
    result = await agent.ainvoke({"messages": [{"role": "user", "content": request_text}]})
    return _extract_text(result)


async def _run_greeting_payload(request_text: str) -> tuple[str, dict[str, Any]]:
    if _is_mock():
        text = await _mock_response(request_text)
        tool_calls = []
        lowered = request_text.lower()
        if _SLEEP_RE.search(request_text):
            tool_calls.append("sleep")
        if "copilot" in lowered:
            tool_calls.append("probe_copilot")
        return text, {
            "display_agent_name": AGENT_ID,
            "display_model_name": _model_name(),
            "display_tool_calls": tool_calls or None,
        }
    agent = await _get_deep_agent()
    result = await agent.ainvoke({"messages": [{"role": "user", "content": request_text}]})
    return _extract_text(result), {
        "display_agent_name": AGENT_ID,
        "display_model_name": _model_name(),
        "display_tool_calls": _extract_tool_calls(result) or None,
    }


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class GreetingsExecutor(AgentExecutor):
    """Accept a task immediately, then complete it from a background worker.

    The a2a-sdk closes the event queue as soon as ``execute()`` returns, so the
    worker publishes progress by writing directly to the task store (which
    ``tasks/get`` polling reads) and pushes notifications manually; the final
    event on the tapped queue is belt-and-braces only.
    """

    def __init__(
        self,
        task_store: TaskStore | None = None,
        push_sender: PushNotificationSender | None = None,
    ) -> None:
        self._task_store = task_store or TASK_STORE
        self._push_sender = push_sender
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()

    def _drop_worker(self, task_id: str, _task: asyncio.Task[None]) -> None:
        self._workers.pop(task_id, None)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or str(uuid.uuid4())
        context_id = context.context_id or str(uuid.uuid4())
        request_text = _request_text(context.message)

        if not request_text:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_text_message("Request contained no text."),
                        timestamp=_utc_now_iso(),
                    ),
                )
            )
            return

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=False,
                status=TaskStatus(
                    state=TaskState.submitted,
                    message=_text_message(f"Accepted: {request_text}"),
                    timestamp=_utc_now_iso(),
                ),
            )
        )

        worker = asyncio.create_task(
            self._complete_later(
                event_queue.tap(),
                task_id=task_id,
                context_id=context_id,
                request_text=request_text,
            )
        )
        # Keep a strong reference so the worker can't be garbage-collected mid-task.
        self._workers[task_id] = worker

        def _on_done(done_task: asyncio.Task[None]) -> None:
            self._drop_worker(task_id, done_task)

        worker.add_done_callback(_on_done)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        self._cancelled.add(task_id)
        worker = self._workers.get(task_id)
        if worker is not None and not worker.done():
            worker.cancel()
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context.context_id or "",
                final=True,
                status=TaskStatus(
                    state=TaskState.canceled,
                    message=_text_message("Cancelled."),
                    timestamp=_utc_now_iso(),
                ),
            )
        )

    async def _complete_later(
        self,
        tapped_queue: EventQueue,
        *,
        task_id: str,
        context_id: str,
        request_text: str,
    ) -> None:
        try:
            # Let the request handler persist the submitted snapshot first.
            await asyncio.sleep(0.1)
            working = await self._save_state(
                task_id, context_id, TaskState.working, "Working on your greeting..."
            )
            await self._notify(working)

            try:
                text, metadata = await _run_greeting_payload(request_text)
                state = TaskState.completed
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Greeting task %s failed", task_id)
                text = f"Greetings agent failed: {exc}"
                state = TaskState.failed
                metadata = {"display_agent_name": AGENT_ID}

            if task_id in self._cancelled:
                return

            terminal = await self._save_state(task_id, context_id, state, text, metadata=metadata)
            await tapped_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    final=True,
                    status=terminal.status,
                )
            )
            await self._notify(terminal)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Greeting worker for task %s crashed", task_id)
            try:
                await self._save_state(
                    task_id, context_id, TaskState.failed, "Greetings agent crashed unexpectedly."
                )
            except Exception:
                logger.exception("Failed to record crash for task %s", task_id)

    async def _save_state(
        self,
        task_id: str,
        context_id: str,
        state: TaskState,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Read-modify-write the stored task; the store is what pollers observe."""
        message = _text_message(text)
        if metadata:
            message.metadata = {k: v for k, v in metadata.items() if v not in (None, "", [])}
        status = TaskStatus(state=state, message=message, timestamp=_utc_now_iso())
        task = await self._task_store.get(task_id)
        if task is None:
            task = Task(
                id=task_id,
                context_id=context_id,
                history=[],
                status=status,
                metadata=dict(message.metadata or {}),
            )
        else:
            task.status = status
            if metadata:
                task.metadata = {**dict(task.metadata or {}), **dict(message.metadata or {})}
        if status.message is not None:
            history = list(task.history or [])
            history.append(status.message)
            task.history = history
        await self._task_store.save(task)
        return task

    async def _notify(self, task: Task) -> None:
        if self._push_sender is None:
            return
        try:
            await self._push_sender.send_notification(task)
        except Exception:
            logger.debug("Push notification failed for task %s", task.id, exc_info=True)


# ---------------------------------------------------------------------------
# Agent card + app wiring
# ---------------------------------------------------------------------------

AGENT_CARD = AgentCard(
    name=AGENT_ID,
    description=AGENT_DESCRIPTION,
    version=AGENT_VERSION,
    url=f"http://{_HOST_LABEL}:{_PORT}",
    capabilities=AgentCapabilities(
        push_notifications=True,
        streaming=False,
        state_transition_history=True,
    ),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id="greet",
            name="Greet",
            description="Reply with a short friendly greeting.",
            tags=["test", "greeting"],
            examples=["Say hello to Tama"],
        ),
        AgentSkill(
            id="sleepy_greeting",
            name="Sleepy greeting",
            description=(
                "Pretend to work (sleep N seconds) before greeting; exercises async task tracking."
            ),
            tags=["test", "async", "sleep"],
            examples=["Sleep 20 seconds then greet me"],
        ),
        AgentSkill(
            id="copilot_probe",
            name="Copilot probe",
            description=(
                "Ask the local GitHub Copilot CLI over ACP to say hello back and relay its reply."
            ),
            tags=["test", "acp", "copilot"],
            examples=["Ask copilot to say hello back"],
        ),
    ],
)


def agent_card() -> dict[str, Any]:
    """JSON payload of the agent card (used by tests)."""
    payload = AGENT_CARD.model_dump(mode="json", exclude_none=True)
    return dict(payload) if isinstance(payload, dict) else {}


TASK_STORE = InMemoryTaskStore()
PUSH_CONFIG_STORE = InMemoryPushNotificationConfigStore()
_push_httpx = httpx.AsyncClient(timeout=10.0)
PUSH_SENDER = BasePushNotificationSender(_push_httpx, PUSH_CONFIG_STORE)
_handler = DefaultRequestHandler(
    agent_executor=GreetingsExecutor(task_store=TASK_STORE, push_sender=PUSH_SENDER),
    task_store=TASK_STORE,
    push_config_store=PUSH_CONFIG_STORE,
    push_sender=PUSH_SENDER,
)
app = A2AFastAPIApplication(agent_card=AGENT_CARD, http_handler=_handler).build()


async def _health(_request: Any) -> JSONResponse:
    return JSONResponse(
        content={
            "status": "healthy",
            "name": AGENT_ID,
            "version": AGENT_VERSION,
            "mock": _is_mock(),
            "timestamp": _utc_now_iso(),
        }
    )


app.add_route("/health", _health, methods=["GET"])


@asynccontextmanager
async def _lifespan(_app: Any) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await _push_httpx.aclose()


app.router.lifespan_context = _lifespan


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=_BIND_HOST, port=_PORT)
