"""Greetings Agent — minimal standalone A2A test agent.

A deliberately tiny, fully isolated A2A server used to exercise Progenitor's
A2A tooling end-to-end (register, call_agent, heartbeat polling, push
callbacks, and live progress streaming). It wraps a deepagents deep agent
that replies with a friendly greeting and has three tools:

- ``sleep`` — pretend work for N seconds, to exercise async tracked invocation.
- ``probe_copilot`` — spawn the GitHub Copilot CLI over ACP, ask it to say
  hello back, and relay the reply.
- ``do_step`` — perform one named step of a multi-step task; the agent calls
  it once per step, giving several distinct, observable tool calls per turn
  to test live progress streaming against (see "do a 3-step task: a, b, c").
- ``pick_greeting_style`` — randomly pick a cultural greeting style (American,
  Mexican, Australian, ...) to greet the caller back with; called for plain
  greetings like "hi" or "hello".

Everything lives in this file and in memory. It imports nothing from
``app.progenitor`` or other ``app.agents.*`` packages — only third-party
libraries. Set ``PROGENITOR_MOCK_LLM=true`` for a deterministic no-LLM mode
(used by the unit tests): canned greetings, real sleeps for "sleep N", a
forced error for requests containing "fail", simulated do_step calls for
"an N-step task", and a simulated pick_greeting_style call for a bare
greeting.

Note on blocking semantics: ``execute()`` runs the whole greeting to
completion before returning (see GreetingsExecutor's docstring for why --
the a2a-sdk closes the event queue as soon as ``execute()`` returns, so
returning early would silently drop every later event). The A2A framework
itself keeps a non-blocking ``message/send`` caller from waiting on that,
by continuing to consume events in the background after handing back an
early "submitted" response.

Run:  python -m uvicorn app.agents.greetings_agent.agent:app --port 8109
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import re
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
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
from dotenv import load_dotenv
from fastapi.responses import JSONResponse
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

# Load this agent's own .env (not the repo-root one) to keep it self-contained.
load_dotenv(Path(__file__).with_name(".env"))

logger = logging.getLogger("greetings_agent")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENT_ID = "greetings_agent"
AGENT_DESCRIPTION = (
    "Minimal A2A test agent: replies with a friendly greeting in a random cultural "
    "style, can pretend to work (sleep) before responding, can probe the local "
    "GitHub Copilot CLI over ACP to have it say hello back, and can perform a "
    "multi-step task to exercise live progress streaming."
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


@tool
async def do_step(step_number: int, step_name: str, seconds: float = 1.0) -> str:
    """Perform one named step of a multi-step task: wait `seconds`, then report completion.

    Call this once per step, in order (step_number starting at 1), when asked to do a
    multi-step task. Exists purely to give the A2A layer several distinct, observable tool
    calls per turn to test live progress streaming against.
    """
    duration = max(0.0, min(float(seconds), GREETINGS_SLEEP_MAX))
    await asyncio.sleep(duration)
    return f"Step {step_number} ({step_name}) done after {duration:.1f}s."


# (style name, example phrase in that style) -- deliberately a plain module-level
# constant, not an enum/class, so the mock path and tests can pick from it directly.
_GREETING_STYLES: tuple[tuple[str, str], ...] = (
    ("American", "Hey there, what's up?"),
    ("British", "Alright? How do you do?"),
    ("Australian", "G'day, mate!"),
    ("Mexican", "¡Hola, amigo! ¿Qué tal?"),
    ("French", "Bonjour! Comment ça va?"),
    ("Japanese", "Konnichiwa!"),
    ("Irish", "Top o' the mornin' to ya!"),
    ("Italian", "Ciao! Come stai?"),
    ("Indian", "Namaste!"),
    ("Brazilian", "Oi, tudo bem?"),
)


def _pick_greeting_style() -> tuple[str, str]:
    """Separated from the `@tool`-wrapped function below so mock mode and tests can
    call (and monkeypatch) it directly, the same way `_probe_copilot` is."""
    return random.choice(_GREETING_STYLES)


@tool
def pick_greeting_style() -> str:
    """Randomly pick a cultural greeting style (e.g. American, Mexican, Australian) to
    greet the caller back with. Returns the style name and an example phrase in that
    style -- call this once, then use it to shape your own reply."""
    style, phrase = _pick_greeting_style()
    return f"{style}: {phrase}"


_SYSTEM_PROMPT = """\
You are greetings_agent, a tiny test agent. Your only job is to reply with a short,
friendly greeting (1-2 sentences).

- If asked your name or who/what you are, say you are "greetings_agent".
- Repeat back what the caller said as part of your greeting, so it's clear you read it.
- If the request asks you to sleep, wait, or take your time for some number of seconds,
  call the `sleep` tool with that duration first, then greet.
- If the request mentions Copilot, call the `probe_copilot` tool to ask the local GitHub
  Copilot CLI to say hello back, and include its reply in your response.
- If the request asks for a multi-step task (e.g. "do a 3-step task" or names several
  steps), call the `do_step` tool once per step in order (step_number starting at 1, using
  a short descriptive step_name for each), then summarize all the steps once done.
- If the message is just a greeting (e.g. "hi", "hello", "hey", "good morning"), call the
  `pick_greeting_style` tool first, then greet the caller back using that cultural style.
- Otherwise just greet the caller warmly. Keep replies brief.
"""

_TOOLS = (sleep, probe_copilot, do_step, pick_greeting_style)

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
                    tools=list(_TOOLS),
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


# Trigger words mirror the system prompt's own promise ("sleep, wait, or take
# your time for some number of seconds") -- the original `sleep\s+(\d+)`
# required the literal word "sleep" immediately followed by whitespace and a
# digit, so it silently missed every other phrasing the prompt itself
# advertises (e.g. "please wait 10 seconds"), and even missed "sleepy ...":
# the \s+ right after "sleep" never matches the "y" in "sleepy". The
# non-digit gap is now a bounded, non-greedy \D{0,24}? so the trigger word
# and the number don't have to be adjacent ("sleep for 10 seconds", "take
# your time, wait about 15 seconds").
_SLEEP_RE = re.compile(
    r"(?:sleep|wait|take your time|pretend to work|hold on)\D{0,24}?(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_STEPS_RE = re.compile(r"(\d+)[\s-]*step", re.IGNORECASE)
_BARE_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|howdy|greetings|yo|good\s+(morning|afternoon|evening))\b",
    re.IGNORECASE,
)
# High enough to comfortably overflow the chat UI's fixed-height A2A card
# body (a manual "how does it behave with way more updates than fit on
# screen" stress test), while still bounded so a mistyped huge number in a
# request can't hang a mock-mode run for minutes.
_MAX_MOCK_STEPS = 50

ProgressCallback = Callable[[str], Awaitable[None]] | None


def _parse_step_names(request_text: str, count: int) -> list[str]:
    """Pull step names from an optional "...: name, name, name" suffix; pad/truncate to count."""
    _prefix, _sep, rest = request_text.partition(":")
    names = [n.strip() for n in rest.split(",") if n.strip()][:count]
    names += [f"step {i}" for i in range(len(names) + 1, count + 1)]
    return names


_MOCK_STEP_DELAY_SECONDS = 0.4


async def _run_mock_steps(
    names: list[str], progress_callback: ProgressCallback = None
) -> list[str]:
    completed: list[str] = []
    for i, name in enumerate(names, start=1):
        if progress_callback is not None:
            # Bookend with the same "Calling X..." / "X finished." framing
            # _report_message_progress uses for the real (non-mock) path, so
            # a mock run's live progress actually exercises the do_step
            # lifecycle that _run_greeting_payload's display_tool_calls
            # metadata claims this request made -- not just a generic label.
            await progress_callback("Calling do_step...")
            await progress_callback(f"Step {i}/{len(names)}: {name}...")
        # Deliberately slower than the minimum needed for correctness: fast
        # enough for the test suite, slow enough that live progress in the
        # chat UI is actually visible to a human watching it stream in.
        await asyncio.sleep(_MOCK_STEP_DELAY_SECONDS)
        completed.append(f"{i}. {name} done")
        if progress_callback is not None:
            await progress_callback("do_step finished.")
    return completed


_SLEEP_CHUNK_SECONDS = 2.0


async def _run_mock_sleep(duration: float, progress_callback: ProgressCallback = None) -> None:
    """Chunk a mocked sleep into periodic "still working" pings instead of one
    silent await, so a sleep-based request produces the same kind of chained
    multi-"working"-update progress a multi-step request does (see
    _run_mock_steps) -- a second, duration-driven way to generate live
    progress events, alongside the step-count-driven one."""
    if progress_callback is not None and duration > 0:
        # Bookend with "Calling sleep..." / "sleep finished." (see
        # _run_mock_steps) so this exercises the same per-tool lifecycle
        # display_tool_calls reports "sleep" as having made, not just the
        # elapsed-time pings below.
        await progress_callback("Calling sleep...")
    remaining = duration
    elapsed = 0.0
    while remaining > 0:
        chunk = min(_SLEEP_CHUNK_SECONDS, remaining)
        await asyncio.sleep(chunk)
        elapsed += chunk
        remaining -= chunk
        if progress_callback is not None:
            await progress_callback(
                f"Still working... {elapsed:.0f}s elapsed of {duration:.0f}s total."
            )
    if progress_callback is not None and duration > 0:
        await progress_callback("sleep finished.")


async def _mock_response(request_text: str, progress_callback: ProgressCallback = None) -> str:
    """Deterministic handler for PROGENITOR_MOCK_LLM mode (no API key needed)."""
    lowered = request_text.lower()
    steps_match = _STEPS_RE.search(request_text)
    if steps_match:
        count = max(1, min(int(steps_match.group(1)), _MAX_MOCK_STEPS))
        names = _parse_step_names(request_text, count)
        completed = await _run_mock_steps(names, progress_callback)
        return "Hello! Completed a " + str(count) + "-step task:\n" + "\n".join(completed)
    match = _SLEEP_RE.search(request_text)
    if match:
        duration = min(float(match.group(1)), GREETINGS_SLEEP_MAX)
        await _run_mock_sleep(duration, progress_callback)
        return f"Hello! I pretended to work for {duration:g} seconds."
    if "fail" in lowered:
        raise RuntimeError("Intentional failure requested by mock input")
    if "copilot" in lowered:
        if progress_callback is not None:
            await progress_callback("Calling probe_copilot...")
        reply = await _probe_copilot("Say hello back in one short sentence.")
        if progress_callback is not None:
            await progress_callback("probe_copilot finished.")
        return f"Copilot says: {reply}"
    if _BARE_GREETING_RE.match(request_text):
        if progress_callback is not None:
            await progress_callback("Calling pick_greeting_style...")
        style, phrase = _pick_greeting_style()
        if progress_callback is not None:
            await progress_callback("pick_greeting_style finished.")
        return (
            f"Hello from greetings_agent! You said: {request_text!r}. "
            f"{style}-style right back at you: {phrase}"
        )
    return f"Hello from greetings_agent! You said: {request_text!r}"


async def _report_message_progress(msg: Any, progress_callback: ProgressCallback) -> None:
    """Best-effort: relay a streamed LangGraph message as a progress update.

    Defensive by design -- this reads duck-typed AIMessage/ToolMessage shapes
    from agent.astream(..., stream_mode="updates"), which isn't exercised by
    any test in this repo against a real LLM (only PROGENITOR_MOCK_LLM=true
    is), so a shape mismatch here must degrade to "no progress event" rather
    than break the actual greeting response.
    """
    if progress_callback is None:
        return
    try:
        tool_calls = getattr(msg, "tool_calls", None) or (
            msg.get("tool_calls") if isinstance(msg, dict) else None
        )
        if isinstance(tool_calls, list) and tool_calls:
            for call in tool_calls:
                name = call.get("name") if isinstance(call, dict) else None
                if name:
                    await progress_callback(f"Calling {name}...")
            return
        if type(msg).__name__ == "ToolMessage":
            name = getattr(msg, "name", None) or (
                msg.get("name") if isinstance(msg, dict) else None
            )
            if name:
                await progress_callback(f"{name} finished.")
    except Exception:
        logger.warning("Progress callback failed while relaying a streamed message", exc_info=True)


async def _run_deep_agent(
    request_text: str, progress_callback: ProgressCallback = None
) -> dict[str, Any]:
    """Run the deep agent via astream (not ainvoke) so each tool call/result along the way
    can be relayed through progress_callback. stream_mode=["updates", "values"] gets both
    per-node deltas (to observe tool calls) and the final accumulated state in one pass."""
    agent = await _get_deep_agent()
    input_state = {"messages": [{"role": "user", "content": request_text}]}
    final_values: dict[str, Any] = input_state
    async for mode, payload in agent.astream(input_state, stream_mode=["updates", "values"]):
        if mode == "values":
            if isinstance(payload, dict):
                final_values = payload
            continue
        if mode != "updates" or not isinstance(payload, dict):
            continue
        for node_output in payload.values():
            if not isinstance(node_output, dict):
                continue
            messages = node_output.get("messages")
            if not isinstance(messages, list):
                continue
            for msg in messages:
                await _report_message_progress(msg, progress_callback)
    return final_values


async def _run_greeting_payload(
    request_text: str, progress_callback: ProgressCallback = None
) -> tuple[str, dict[str, Any]]:
    if _is_mock():
        text = await _mock_response(request_text, progress_callback)
        lowered = request_text.lower()
        tool_calls: list[str] = []
        # Mirrors _mock_response's own branch precedence exactly (steps >
        # sleep > fail > copilot > bare greeting > plain) via elif, not
        # independent ifs -- _mock_response returns after its first match, so
        # at most one of these ever actually ran. Independent ifs previously
        # let a later branch's trigger word (now much easier to hit since the
        # sleep regex broadened to "wait"/"take your time"/etc, see _SLEEP_RE)
        # append a phantom tool call for a phase that never executed, e.g. a
        # steps request that also happens to contain "wait" reporting a
        # "sleep" call it never made. "fail" needs no branch here: it raises
        # inside _mock_response above, so execution never reaches this code.
        steps_match = _STEPS_RE.search(request_text)
        if steps_match:
            tool_calls.extend(["do_step"] * max(1, min(int(steps_match.group(1)), _MAX_MOCK_STEPS)))
        elif _SLEEP_RE.search(request_text):
            tool_calls.append("sleep")
        elif "copilot" in lowered:
            tool_calls.append("probe_copilot")
        elif _BARE_GREETING_RE.match(request_text):
            tool_calls.append("pick_greeting_style")
        return text, {
            "display_agent_name": AGENT_ID,
            "display_model_name": _model_name(),
            "display_tool_calls": tool_calls or None,
        }
    result = await _run_deep_agent(request_text, progress_callback)
    return _extract_text(result), {
        "display_agent_name": AGENT_ID,
        "display_model_name": _model_name(),
        "display_tool_calls": _extract_tool_calls(result) or None,
    }


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class GreetingsExecutor(AgentExecutor):
    """Runs one greeting per execute() call, awaited to completion.

    execute() must not return until the greeting is done: the a2a-sdk closes
    `event_queue` the instant execute() returns
    (DefaultRequestHandler._run_event_stream), so a previous design that
    completed the task from a detached background worker could only publish
    progress by writing directly to the task store, and treated its own
    final event push as "belt-and-braces" best-effort, because it knew that
    push likely wouldn't reach a live listener. The framework already
    provides "don't make a non-blocking caller wait" on its own
    (ResultAggregator.consume_and_break_on_interrupt continues consuming in
    a background task after returning an early response), so execute()
    running the whole greeting inline is both necessary and sufficient --
    and is what makes live streaming (AgentCapabilities.streaming) genuinely
    work rather than silently drop everything past the first event.

    Multiple concurrent greetings are expected and supported (unlike Builder
    Agent, nothing here is single-flight) -- each execute() call tracks its
    own worker task purely so cancel() has something to interrupt.
    """

    def __init__(
        self,
        push_sender: PushNotificationSender | None = None,
    ) -> None:
        # Kept for constructor compatibility; DefaultRequestHandler owns push
        # delivery for every event emitted by this executor.
        _ = push_sender
        self._workers: dict[str, asyncio.Task[None]] = {}
        # The `event_queue` a cancel() call receives is a *fresh* tap
        # (on_cancel_task does its own `_queue_manager.tap(task.id)`), not
        # the one execute() was originally given -- pushing only to that
        # fresh tap satisfies on_cancel_task's own validation (its RPC
        # response correctly shows `canceled`) but never reaches a caller
        # who is actively watching the original message/stream connection,
        # since tapping is a one-way parent-to-child forward, not shared
        # between sibling taps. Tracking the original queue per task_id lets
        # cancel() push the terminal event to the connection that's actually
        # live, not just to its own throwaway one.
        self._event_queues: dict[str, EventQueue] = {}
        # execute() and cancel() both end up awaiting the same worker task
        # once cancel() calls worker.cancel() -- asyncio does not guarantee
        # which of the two awaiters gets woken first. Without this, execute()
        # can win the race, return, and let the framework close event_queue
        # before cancel() has pushed its terminal event to it, silently
        # dropping it and leaving a live message/stream watcher hanging
        # forever (confirmed empirically). This event makes the ordering
        # explicit instead of relying on scheduling luck: execute() waits
        # for cancel() to finish before it's allowed to return.
        self._cancel_handled: dict[str, asyncio.Event] = {}

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
            self._run(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                request_text=request_text,
            )
        )
        self._workers[task_id] = worker
        self._event_queues[task_id] = event_queue
        cancel_handled = asyncio.Event()
        self._cancel_handled[task_id] = cancel_handled
        try:
            await worker
        except asyncio.CancelledError:
            # cancel() owns pushing the terminal event (see its docstring) --
            # wait for it to actually finish before returning, so the
            # framework doesn't close event_queue out from under it (see
            # _cancel_handled's comment). execute() must still return
            # normally afterwards -- not propagate this -- since
            # DefaultRequestHandler._run_event_stream only closes
            # event_queue after execute() returns; if it raised instead, the
            # queue would never close and any live message/stream watcher
            # would hang indefinitely rather than seeing the stream end.
            await cancel_handled.wait()
        finally:
            self._workers.pop(task_id, None)
            self._event_queues.pop(task_id, None)
            self._cancel_handled.pop(task_id, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel a running greeting task through the handler's event queue.

        ``DefaultRequestHandler.on_cancel_task`` supplies a child tap so it can
        consume the cancellation result, while the message/send or
        message/stream handler keeps consuming the original queue.  Enqueueing
        exactly once on that original queue fans the same event to both
        consumers.  The handler's existing push-notification callback then
        owns delivery, so cancellation follows the same exactly-once path as
        normal terminal completion instead of sending a second notification
        directly from the executor.

        Claims the worker with ``pop`` so concurrent cancellation requests have
        one terminal-event owner.  A worker cancelled before its coroutine gets
        a first turn never runs its body, so this method must publish the
        cancellation event itself after awaiting the worker's cleanup.
        """
        task_id = context.task_id or ""
        context_id = context.context_id or ""
        worker = self._workers.pop(task_id, None)
        if worker is None or worker.done():
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.canceled,
                        message=_text_message("No active task to cancel."),
                        timestamp=_utc_now_iso(),
                    ),
                )
            )
            return

        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await worker

        terminal = await self._save_state(task_id, context_id, TaskState.canceled, "Cancelled.")
        # ``event_queue`` is the fresh cancellation tap.  Publish through the
        # original queue when it is still active: its fan-out delivers this
        # one event to both the cancellation handler and the live request
        # handler, whose push callback is the sole notification owner.
        live_queue = self._event_queues.get(task_id)
        notification_queue = live_queue or event_queue
        await notification_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id, context_id=context_id, final=True, status=terminal.status
            )
        )

        # Release execute(), which is waiting on this (see _cancel_handled's
        # comment in __init__) so it can't return -- and let the framework
        # close the original queue -- until the terminal event is enqueued.
        cancel_handled = self._cancel_handled.get(task_id)
        if cancel_handled is not None:
            cancel_handled.set()

    async def _run(
        self,
        event_queue: EventQueue,
        *,
        task_id: str,
        context_id: str,
        request_text: str,
    ) -> None:
        async def _progress(step_text: str) -> None:
            step = await self._save_state(task_id, context_id, TaskState.working, step_text)
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id, context_id=context_id, final=False, status=step.status
                )
            )

        working = await self._save_state(
            task_id, context_id, TaskState.working, "Working on your greeting..."
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id, context_id=context_id, final=False, status=working.status
            )
        )
        # No self._notify() call here (or after the terminal event below):
        # DefaultRequestHandler already sends a push notification through
        # the same push_sender after it processes each event this pushes
        # through event_queue (both message/send and message/stream wire
        # this up) -- calling it here too would deliver every transition to
        # push subscribers twice.

        # No CancelledError handling here: cancel() owns pushing the
        # terminal event (see its docstring for why), so on cancellation
        # this just propagates uncaught, same as any other coroutine that
        # doesn't catch its own cancellation.
        try:
            text, metadata = await _run_greeting_payload(request_text, _progress)
            state = TaskState.completed
        except Exception as exc:
            logger.exception("Greeting task %s failed", task_id)
            text = f"Greetings agent failed: {exc}"
            state = TaskState.failed
            metadata = {"display_agent_name": AGENT_ID}

        terminal = await self._save_state(task_id, context_id, state, text, metadata=metadata)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id, context_id=context_id, final=True, status=terminal.status
            )
        )

    async def _save_state(
        self,
        task_id: str,
        context_id: str,
        state: TaskState,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Build the Task object for one status transition.

        Deliberately does not touch a task store: every TaskStatusUpdateEvent
        this builds gets pushed through event_queue and independently
        persisted by the framework's own TaskManager once it consumes that
        event (DefaultRequestHandler wires this for every message/send and
        message/stream call). Persisting here too produced not just a
        duplicate history entry per transition but sometimes a *stale* one --
        TaskManager caches its own in-memory Task across the whole request
        instead of re-reading the store after its first event, so a write
        made here after that point was invisible to it, and it would
        re-append whatever message its own stale copy still had instead.
        """
        message = _text_message(text)
        if metadata:
            message.metadata = {k: v for k, v in metadata.items() if v not in (None, "", [])}
        status = TaskStatus(state=state, message=message, timestamp=_utc_now_iso())
        return Task(
            id=task_id,
            context_id=context_id,
            history=[],
            status=status,
            metadata=dict(message.metadata or {}),
        )



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
        streaming=True,
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
        AgentSkill(
            id="multi_step_task",
            name="Multi-step task",
            description=(
                "Perform a multi-step task, calling the do_step tool once per step -- exercises "
                "live per-tool-call progress streaming (several observable steps per turn)."
            ),
            tags=["test", "progress", "streaming"],
            examples=["Do a 3-step task: prep, build, verify"],
        ),
        AgentSkill(
            id="cultural_greeting",
            name="Cultural greeting",
            description=(
                "Pick a random cultural greeting style (American, Mexican, Australian, ...) "
                "and greet the caller back using it."
            ),
            tags=["test", "greeting", "random"],
            examples=["Hi", "Hello there"],
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
    agent_executor=GreetingsExecutor(push_sender=PUSH_SENDER),
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
