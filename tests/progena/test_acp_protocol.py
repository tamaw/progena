"""Wire-level ACP behavior exercised against the SDK agent-side connection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from acp import update_agent_message_text
from acp.agent import AgentSideConnection
from acp.schema import (
    ElicitationFormSessionMode,
    ElicitationSchema,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    ResumeSessionResponse,
    SetSessionConfigOptionResponse,
    SetSessionModeResponse,
    ToolCallUpdate,
)

from src.progena.acp.runner import ACPRunner


@dataclass
class _WireState:
    prompts: list[str] = field(default_factory=list)
    steered: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    elicitation_answers: list[dict[str, Any] | None] = field(default_factory=list)
    permission_outcomes: list[str] = field(default_factory=list)
    prompt_started: asyncio.Event = field(default_factory=asyncio.Event)
    injected: asyncio.Event = field(default_factory=asyncio.Event)


class _WireAgent:
    def __init__(self, state: _WireState) -> None:
        self._state = state
        self._conn: AgentSideConnection | None = None
        self._running = False

    def on_connect(self, conn: AgentSideConnection) -> None:
        self._conn = conn

    async def initialize(
        self, protocol_version: int, client_capabilities: Any, **_kwargs: Any
    ) -> InitializeResponse:
        assert client_capabilities.elicitation.form is not None
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(self, cwd: str, **_kwargs: Any) -> NewSessionResponse:
        _ = cwd
        return NewSessionResponse(session_id="wire-session")

    async def resume_session(
        self, cwd: str, session_id: str, **_kwargs: Any
    ) -> ResumeSessionResponse:
        _ = cwd
        return ResumeSessionResponse(session_id=session_id)

    async def set_session_mode(
        self, session_id: str, mode_id: str, **_kwargs: Any
    ) -> SetSessionModeResponse:
        _ = session_id
        self._state.modes.append(mode_id)
        return SetSessionModeResponse()

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **_kwargs: Any
    ) -> SetSessionConfigOptionResponse:
        _ = (config_id, session_id, value)
        return SetSessionConfigOptionResponse(config_options=[])

    async def prompt(self, session_id: str, prompt: list[Any], **_kwargs: Any) -> PromptResponse:
        assert self._conn is not None
        text = str(prompt[0].text)
        self._state.prompts.append(text)
        self._state.prompt_started.set()
        self._running = True
        try:
            if text == "wait-for-steering":
                await self._state.injected.wait()
                reply = self._state.steered[-1]
            elif text == "ask-question":
                answer = await self._conn.create_elicitation(
                    "Which option?",
                    ElicitationFormSessionMode(
                        session_id=session_id,
                        requested_schema=ElicitationSchema(
                            properties={"choice": {"type": "string"}}
                        ),
                    ),
                )
                self._state.elicitation_answers.append(getattr(answer, "content", None))
                reply = "question-answered"
            elif text.startswith("plan-"):
                response = await self._conn.request_permission(
                    session_id=session_id,
                    tool_call=ToolCallUpdate(
                        tool_call_id="exit-plan",
                        title="ExitPlanMode",
                        kind="other",
                        raw_input={"plan": "proposed steps"},
                    ),
                    options=[
                        PermissionOption(option_id="approve", name="Approve", kind="allow_once"),
                    ],
                )
                self._state.permission_outcomes.append(response.outcome.outcome)
                reply = "plan-handled"
            else:
                reply = text
            await self._conn.session_update(session_id, update_agent_message_text(reply))
            return PromptResponse(stop_reason="end_turn")
        finally:
            self._running = False

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, str]:
        if method != "session/steering" or not self._running:
            return {"outcome": "promptRequired"}
        text = str(params["prompt"][0]["text"])
        self._state.steered.append(text)
        self._state.injected.set()
        return {"outcome": "injected"}


@asynccontextmanager
async def _wire_agent() -> AsyncIterator[tuple[_WireState, int]]:
    state = _WireState()
    connections: set[asyncio.Task[None]] = set()

    async def connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = AgentSideConnection(
            _WireAgent(state), writer, reader, listening=False, use_unstable_protocol=True
        )
        task = asyncio.current_task()
        assert task is not None
        connections.add(task)
        try:
            await connection.listen()
        finally:
            connections.discard(task)
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(connect, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        yield state, port
    finally:
        server.close()
        await server.wait_closed()
        for connection in connections:
            connection.cancel()
        if connections:
            await asyncio.gather(*connections, return_exceptions=True)


async def test_runner_uses_live_connection_for_steering_and_mode() -> None:
    async with _wire_agent() as (state, port):
        runner = ACPRunner(host="127.0.0.1", port=port, timeout=5)
        run = asyncio.create_task(
            runner.run("wait-for-steering", "/tmp", mode="plan", run_id="wire")
        )
        await asyncio.wait_for(state.prompt_started.wait(), timeout=2)
        assert await runner.steer("wire-session", "injected text") == "injected"
        result = await asyncio.wait_for(run, timeout=2)

    assert result.success is True
    assert result.output == "injected text"
    assert state.steered == ["injected text"]
    assert state.modes == ["plan"]


async def test_form_elicitation_and_plan_permissions_round_trip_over_acp() -> None:
    inputs: list[dict[str, Any]] = []

    async def on_input(request: dict[str, Any]) -> dict[str, Any]:
        inputs.append(request)
        if request["kind"] == "question":
            return {"choice": "blue"}
        return {"approve": request["message"] == "ExitPlanMode"}

    async with _wire_agent() as (state, port):
        runner = ACPRunner(host="127.0.0.1", port=port, timeout=5)
        question = await runner.run("ask-question", "/tmp", on_input=on_input)
        approved = await runner.run("plan-approve", "/tmp", on_input=on_input)

        async def decline_input(request: dict[str, Any]) -> dict[str, Any]:
            inputs.append(request)
            return {"approve": False, "feedback": "make the plan smaller"}

        rejected = await runner.run("plan-reject", "/tmp", on_input=decline_input)

    assert question.success and approved.success and rejected.success
    assert state.elicitation_answers == [{"choice": "blue"}]
    assert state.permission_outcomes == ["selected", "cancelled"]
    assert [request["kind"] for request in inputs] == ["question", "plan", "plan"]
    assert inputs[1]["plan"] == "proposed steps"
