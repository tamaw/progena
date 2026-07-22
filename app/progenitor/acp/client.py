"""Generic streaming ACP client.

``ACPSessionClient`` implements the ACP ``Client`` callback interface for *any*
stdio ACP agent. It collects the streaming session updates (agent message text,
thoughts, tool calls) emitted during a ``session/prompt`` and auto-approves
permission requests, since runs are sandboxed to a scoped working directory
(see ``ACPRunner``) rather than gated per tool call.

Generalised from ``app/agents/builder_agent/runtime/copilot_acp.py``
(``ProgenitorACPClient``); nothing here is Copilot-specific.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from acp import Client
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
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
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

if TYPE_CHECKING:
    from acp.terminal import TerminalHandle
else:
    TerminalHandle = Any  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class ACPSessionClient(Client):
    """Collects streaming updates from an ACP agent for one prompt."""

    def __init__(self, run_id: str = "unknown") -> None:
        self.run_id = run_id
        self.text_parts: list[str] = []
        self.thoughts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._conn: Any = None

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
                self.text_parts.append(text)
        elif isinstance(update, AgentThoughtChunk):
            text = getattr(getattr(update, "content", None), "text", None)
            if text:
                self.thoughts.append(text)
        elif isinstance(update, ToolCallStart):
            self.tool_calls.append(
                {
                    "id": getattr(update, "tool_call_id", None),
                    "name": getattr(update, "title", None),
                    "kind": getattr(update, "kind", None),
                    "status": "start",
                }
            )
        elif isinstance(update, ToolCallProgress):
            self.tool_calls.append(
                {
                    "id": getattr(update, "tool_call_id", None),
                    "name": getattr(update, "title", None),
                    "status": str(getattr(update, "status", "") or "progress"),
                }
            )
        # AgentPlanUpdate / AvailableCommandsUpdate / CurrentModeUpdate /
        # SessionInfoUpdate carry no text we surface; ignored.

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **_kwargs: Any,
    ) -> RequestPermissionResponse:
        """Auto-approve tool calls.

        Runs are confined to a sandbox directory by ``ACPRunner`` (passed to the
        agent as its allowed dir), so the filesystem scope — not per-call
        prompting — is the guardrail.
        """
        _ = session_id
        _ = tool_call
        if options and hasattr(options[0], "id"):
            return RequestPermissionResponse(
                outcome=AllowedOutcome(option_id=options[0].id, outcome="selected")
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # --- Minimal client-side I/O stubs (the agent handles these internally) ---

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
        **_kwargs: Any,
    ) -> CreateTerminalResponse | TerminalHandle:
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
        """Aggregated agent message text from the run."""
        return "".join(self.text_parts)
