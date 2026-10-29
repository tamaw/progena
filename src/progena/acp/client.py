"""Generic streaming ACP client.

``ACPSessionClient`` implements the ACP ``Client`` callback interface for any
ACP agent. It collects the streamed session updates (agent message text,
thoughts, plans, and tool calls) emitted during a ``session/prompt``, routes
form elicitations and plan approval requests through an input callback, and
auto-approves ordinary tool permissions.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TextIO

from acp import Client
from acp.schema import (
    AcceptElicitationResponse,
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    ClientCapabilities,
    CreateTerminalResponse,
    DeclineElicitationResponse,
    DeniedOutcome,
    ElicitationCapabilities,
    ElicitationFormCapabilities,
    ElicitationFormRequestMode,
    ElicitationFormSessionMode,
    ElicitationMode,
    EnvVariable,
    KillTerminalResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

logger = logging.getLogger(__name__)

_MAX_METADATA_ENTRIES = 200
_MAX_CAPTURE_PREVIEW_CHARS = 16_384


class ACPSessionClient(Client):
    """Collects streaming updates from an ACP agent for one prompt."""

    def __init__(
        self,
        run_id: str = "unknown",
        on_progress: Callable[[dict[str, Any]], Awaitable[Any] | Any] | None = None,
        on_input: Callable[[dict[str, Any]], Awaitable[Any] | Any] | None = None,
        *,
        max_output_chars: int | None = None,
        max_metadata_entries: int = _MAX_METADATA_ENTRIES,
    ) -> None:
        self.run_id = run_id
        self.text_parts: list[str] = []
        self.thoughts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.output_char_count = 0
        self.output_truncated = False
        self.tool_call_count = 0
        self._retained_output_chars = 0
        self._output_spool: TextIO | None = None
        self._max_output_chars = (
            max_output_chars if max_output_chars and max_output_chars > 0 else None
        )
        self._max_metadata_entries = max(1, max_metadata_entries)
        self.capabilities = ClientCapabilities(
            elicitation=ElicitationCapabilities(form=ElicitationFormCapabilities())
        )
        self._conn: Any = None
        self._on_progress = on_progress
        self._on_input = on_input

    def set_output_limit(self, max_output_chars: int | None) -> None:
        """Start bounded collection after an exact durable evidence source is ready."""
        self._max_output_chars = (
            max_output_chars if max_output_chars is not None and max_output_chars > 0 else None
        )
        if self._max_output_chars is not None:
            if self._output_spool is not None:
                self._output_spool.seek(0)
                self.text_parts = [self._output_spool.read(self._max_output_chars)]
                self.close_output()
            remaining = self._max_output_chars
            retained: list[str] = []
            for part in self.text_parts:
                if remaining <= 0:
                    break
                retained.append(part[:remaining])
                remaining -= len(retained[-1])
            self.text_parts = retained
            self._retained_output_chars = self._max_output_chars - remaining
            self.output_truncated |= self.output_char_count > self._retained_output_chars

    async def _emit_progress(self, update: dict[str, Any]) -> None:
        if self._on_progress is None:
            return
        result = self._on_progress(update)
        if inspect.isawaitable(result):
            await result

    def on_connect(self, conn: Any) -> None:
        self._conn = conn
        logger.info("ACP [%s]: connected to agent", self.run_id)

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **_kwargs: Any,
    ) -> None:
        """Handle one streaming update from the agent (one per chunk)."""
        _ = session_id
        if isinstance(update, AgentMessageChunk):
            text = getattr(getattr(update, "content", None), "text", None)
            if text:
                self.output_char_count += len(text)
                if self._max_output_chars is None:
                    try:
                        if self._output_spool is None:
                            capture_dir = os.environ.get("PROGENA_ACP_CAPTURE_DIR")
                            if capture_dir:
                                Path(capture_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
                            # Callback-spanning lifetime; ACPRunner closes it in finally.
                            self._output_spool = tempfile.TemporaryFile(  # noqa: SIM115
                                mode="w+", encoding="utf-8", dir=capture_dir
                            )
                        self._output_spool.write(text)
                    except OSError as exc:
                        self.errors.append(f"ACP output capture failed: {exc}"[:512])
                        self.output_truncated = True
                        raise
                retention_limit = self._max_output_chars or _MAX_CAPTURE_PREVIEW_CHARS
                remaining = retention_limit - self._retained_output_chars
                if remaining > 0:
                    retained_text = text[:remaining]
                    self.text_parts.append(retained_text)
                    self._retained_output_chars += len(retained_text)
                if self._max_output_chars is not None:
                    self.output_truncated |= len(text) > remaining
                await self._emit_progress({"kind": "message", "text": text})
        elif isinstance(update, AgentThoughtChunk):
            text = getattr(getattr(update, "content", None), "text", None)
            if text:
                self.thoughts.append(text[-4096:])
                if len(self.thoughts) > self._max_metadata_entries:
                    del self.thoughts[: -self._max_metadata_entries]
                await self._emit_progress({"kind": "thought", "text": text})
        elif isinstance(update, AgentPlanUpdate):
            plan = update.model_dump(mode="json").get("entries", [])
            await self._emit_progress({"kind": "plan", "plan": plan})
        elif isinstance(update, ToolCallStart):
            call = {
                "id": str(getattr(update, "tool_call_id", None) or "")[:256],
                "name": str(getattr(update, "title", None) or "")[:512],
                "kind": str(getattr(update, "kind", None) or "")[:128],
                "status": "start",
            }
            self.tool_call_count += 1
            self.tool_calls.append(call)
            if len(self.tool_calls) > self._max_metadata_entries:
                del self.tool_calls[: -self._max_metadata_entries]
            await self._emit_progress({"kind": "tool", "tool_call": call})
        elif isinstance(update, ToolCallProgress):
            call = {
                "id": str(getattr(update, "tool_call_id", None) or "")[:256],
                "name": str(getattr(update, "title", None) or "")[:512],
                "status": str(getattr(update, "status", "") or "progress")[:128],
            }
            self.tool_calls.append(call)
            if len(self.tool_calls) > self._max_metadata_entries:
                del self.tool_calls[: -self._max_metadata_entries]
            await self._emit_progress({"kind": "tool", "tool_call": call})

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **_kwargs: Any,
    ) -> RequestPermissionResponse:
        """Prompt for plan approval and auto-allow ordinary tool permissions."""
        _ = session_id
        if self._is_plan_permission(tool_call):
            answer = await self._request_input(
                {
                    "id": f"input_{uuid.uuid4().hex}",
                    "kind": "plan",
                    "message": str(getattr(tool_call, "title", None) or "Approve plan?"),
                    "plan": self._plan_text(getattr(tool_call, "raw_input", None)),
                    "tool": str(getattr(tool_call, "title", None) or ""),
                    "options": [
                        option.model_dump(mode="json", by_alias=True) for option in options
                    ],
                }
            )
            if isinstance(answer, dict) and answer.get("approve") is True:
                return self._allow_response(options)
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return self._allow_response(options)

    async def create_elicitation(
        self,
        message: str,
        mode: ElicitationMode,
        **_kwargs: Any,
    ) -> AcceptElicitationResponse | DeclineElicitationResponse:
        """Expose form elicitation to the task supervisor; decline URL mode."""
        if not isinstance(mode, (ElicitationFormSessionMode, ElicitationFormRequestMode)):
            return DeclineElicitationResponse(action="decline")
        requested_schema = mode.requested_schema.model_dump(mode="json", by_alias=True)
        answer = await self._request_input(
            {
                "id": f"input_{uuid.uuid4().hex}",
                "kind": "question",
                "message": message,
                "fields": requested_schema.get("properties") or {},
            }
        )
        if not isinstance(answer, dict) or answer.get("action") in {"decline", "cancel"}:
            return DeclineElicitationResponse(action="decline")
        return AcceptElicitationResponse(action="accept", content=answer)

    async def _request_input(self, request: dict[str, Any]) -> Any:
        if self._on_input is None:
            return {"action": "decline"}
        try:
            answer = self._on_input(request)
            return await answer if inspect.isawaitable(answer) else answer
        except Exception:
            logger.exception("ACP [%s]: input request failed", self.run_id)
            return {"action": "decline"}

    @staticmethod
    def _is_plan_permission(tool_call: ToolCallUpdate) -> bool:
        title = str(getattr(tool_call, "title", None) or "").lower()
        kind = str(getattr(tool_call, "kind", None) or "").lower()
        raw_input = getattr(tool_call, "raw_input", None)
        raw_text = json.dumps(raw_input, default=str).lower()
        return "exitplanmode" in title or "plan" in kind or "plan" in raw_text

    @staticmethod
    def _plan_text(raw_input: Any) -> str:
        if isinstance(raw_input, str):
            return raw_input
        if isinstance(raw_input, dict) and isinstance(raw_input.get("plan"), str):
            return raw_input["plan"]
        return json.dumps(raw_input, default=str) if raw_input is not None else ""

    @staticmethod
    def _allow_response(options: list[PermissionOption]) -> RequestPermissionResponse:
        for preferred_kind in ("allow_always", "allow_once"):
            option = next(
                (
                    candidate
                    for candidate in options
                    if getattr(candidate, "kind", None) == preferred_kind
                ),
                None,
            )
            option_id = getattr(option, "option_id", None) if option is not None else None
            if option_id:
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(option_id=option_id, outcome="selected")
                )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # --- Minimal client-side I/O stubs (the agent handles these internally) ---

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **_kwargs: Any,
    ) -> CreateTerminalResponse:
        _ = (session_id, command, args, env, cwd, output_byte_limit)
        return CreateTerminalResponse(terminal_id="internal")

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **_kwargs: Any,
    ) -> ReadTextFileResponse:
        _ = (session_id, path, line, limit)
        return ReadTextFileResponse(content="")

    async def write_text_file(
        self,
        session_id: str,
        path: str,
        content: str,
        **_kwargs: Any,
    ) -> WriteTextFileResponse | None:
        _ = (session_id, path, content)
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
    ) -> KillTerminalResponse | None:
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

    async def complete_elicitation(self, elicitation_id: str, **_kwargs: Any) -> None:
        _ = elicitation_id

    def get_output(self) -> str:
        """Exact generic output, or bounded preview after native source verification."""
        if self._output_spool is not None:
            position = self._output_spool.tell()
            try:
                self._output_spool.seek(0)
                return self._output_spool.read()
            finally:
                self._output_spool.seek(position)
        return "".join(self.text_parts)

    def close_output(self) -> None:
        """Release the private anonymous capture file after result materialization."""
        if self._output_spool is not None:
            self._output_spool.close()
            self._output_spool = None
