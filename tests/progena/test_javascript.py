"""Behavioral boundaries for Progena's sandboxed JavaScript tool."""

import json
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver

from src.progena.agent.adapters import create_deepagent_tools
from src.progena.agent.factory import _create_javascript_middleware
from src.progena.agent.middleware.schema_compaction import SchemaCompactionMiddleware


class _ToolCallingModel(FakeMessagesListChatModel):
    """Scripted model that accepts the graph's dynamically supplied tools."""

    def bind_tools(self, tools: Sequence[BaseTool], **kwargs):
        return self


def _javascript_call(code: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "javascript", "args": {"code": code}, "id": call_id}],
    )


def _graph_for(*codes: str, tools: Sequence[BaseTool] = ()):
    responses = [_javascript_call(code, f"javascript-{index}") for index, code in enumerate(codes)]
    responses.append(AIMessage(content="complete"))
    return create_agent(
        _ToolCallingModel(responses=responses),
        tools=list(tools),
        middleware=[SchemaCompactionMiddleware(), _create_javascript_middleware()],
    )


def _tool_results(result: dict[str, Any]) -> list[str]:
    results: list[str] = []
    for message in result["messages"]:
        if isinstance(message, ToolMessage):
            assert isinstance(message.content, str)
            results.append(message.content)
    return results


def test_javascript_has_no_host_or_unlisted_parent_tool_access() -> None:
    @tool
    def parent_secret() -> str:
        """A parent tool which must not become a JavaScript host API."""
        return "secret"

    @tool
    def execute(command: str) -> str:
        """An execution-capable parent tool that must not cross the JS bridge."""
        return command

    @tool
    def shell_exec(command: str) -> str:
        """A shell-capable parent tool that must not cross the JS bridge."""
        return command

    graph = _graph_for(
        "JSON.stringify([typeof process, typeof require, typeof fetch, typeof Deno, "
        "typeof fs, typeof tools.parentSecret, typeof tools.execute, typeof tools.shellExec])",
        tools=[parent_secret, execute, shell_exec],
    )

    result = graph.invoke({"messages": [HumanMessage(content="inspect runtime")]})

    assert _tool_results(result) == [
        '<result>["undefined","undefined","undefined","undefined","undefined",'
        '"undefined","undefined","undefined"]</result>'
    ]


async def test_javascript_reuses_turn_globals_but_isolates_new_turns_and_threads() -> None:
    graph = create_agent(
        _ToolCallingModel(
            responses=[
                _javascript_call("globalThis.previousCall = 17", "javascript-set"),
                _javascript_call("previousCall + 1", "javascript-reuse"),
                AIMessage(content="set"),
                _javascript_call("typeof previousCall", "javascript-read"),
                AIMessage(content="read"),
                _javascript_call("typeof previousCall", "javascript-resume"),
                AIMessage(content="resumed"),
            ]
        ),
        tools=[],
        middleware=[_create_javascript_middleware()],
        checkpointer=InMemorySaver(),
    )

    first = await graph.ainvoke(
        {"messages": [HumanMessage(content="set a global")]},
        {"configurable": {"thread_id": "javascript-first"}},
    )
    second = await graph.ainvoke(
        {"messages": [HumanMessage(content="inspect a new thread")]},
        {"configurable": {"thread_id": "javascript-second"}},
    )
    resumed = await graph.ainvoke(
        {"messages": [HumanMessage(content="inspect the original thread in a new turn")]},
        {"configurable": {"thread_id": "javascript-first"}},
    )

    assert _tool_results(first) == ["<result>17</result>", "<result>18</result>"]
    assert _tool_results(second) == ["<result>undefined</result>"]
    assert _tool_results(resumed)[-1] == "<result>undefined</result>"


def test_javascript_recovers_after_timeout_and_heap_exhaustion() -> None:
    graph = _graph_for(
        "while (true) {}",
        "6 * 7",
        "new ArrayBuffer(128 * 1024 * 1024)",
        "21 * 2",
    )

    result = graph.invoke({"messages": [HumanMessage(content="exercise limits")]})

    timeout, after_timeout, exhausted, after_exhaustion = _tool_results(result)
    assert 'type="Timeout"' in timeout
    assert after_timeout == "<result>42</result>"
    assert 'type="OutOfMemory"' in exhausted
    assert after_exhaustion == "<result>42</result>"


def test_javascript_truncates_console_and_result_output() -> None:
    graph = _graph_for("console.log('x'.repeat(5000)); 'y'.repeat(5000)")

    result = _tool_results(graph.invoke({"messages": [HumanMessage(content="produce output")]}))[0]

    assert result.startswith("<stdout>\n")
    assert "</stdout>\n<result>" in result
    assert result.count("… [truncated ") == 2
    assert 0 < result.count("x") < 5000
    assert 0 < result.count("y") < 5000


class _A2AState:
    """Minimal persisted-task store used by the real system-tool adapter."""

    def __init__(self) -> None:
        self.records: dict[str, Any] = {}

    async def get_current_state(self) -> dict[str, str]:
        return {"thread_id": "javascript-bridge"}

    async def get_a2a_task(self, task_id: str) -> Any | None:
        return self.records.get(task_id)

    async def get_a2a_tasks(self) -> list[Any]:
        return list(self.records.values())

    async def upsert_a2a_task(self, record: Any) -> None:
        self.records[record.task_id] = record


class _A2ARegistry:
    """Registry surface consumed by Progena's A2A system tools."""

    def __init__(self, base_url: str) -> None:
        self._agent = {
            "name": "deterministic-a2a",
            "url": base_url,
            "transport": "http",
        }

    async def load(self) -> None:
        return None

    def find_by_name(self, name: str) -> dict[str, str] | None:
        return self._agent if name == self._agent["name"] else None


@contextmanager
def _deterministic_a2a_service() -> Iterator[tuple[ThreadingHTTPServer, list[str]]]:
    """Serve a small A2A JSON-RPC peer so the adapter crosses real HTTP."""

    received: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _respond(self, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/.well-known/agent-card.json":
                self.send_error(404)
                return
            self._respond(
                {
                    "name": "deterministic-a2a",
                    "description": "Deterministic JavaScript bridge test peer",
                    "url": f"{base_url}/rpc",
                    "version": "1.0",
                    "capabilities": {
                        "streaming": False,
                        "pushNotifications": False,
                        "stateTransitionHistory": False,
                    },
                    "defaultInputModes": ["text/plain"],
                    "defaultOutputModes": ["text/plain"],
                    "skills": [],
                }
            )

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/rpc":
                self.send_error(404)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = payload["method"]
            if method == "message/send":
                parts = payload["params"]["message"]["parts"]
                received.append(str(parts[0]["text"]))
            elif method != "tasks/get":
                self.send_error(400)
                return
            self._respond(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "kind": "task",
                        "id": "task-local",
                        "contextId": "context-local",
                        "status": {
                            "state": "completed",
                            "message": {
                                "role": "agent",
                                "messageId": "message-local",
                                "parts": [{"kind": "text", "text": "remote: received"}],
                            },
                        },
                    },
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_javascript_reads_scoped_backend_data_but_not_write_or_exec_tools(
    tmp_path: Path,
) -> None:
    mounted_root = tmp_path / "mounted"
    mounted_root.mkdir()
    (mounted_root / "permitted.txt").write_text("visible through the selected bridge")
    (tmp_path / "unmounted-secret.txt").write_text("must not cross the selected bridge")

    @tool
    def execute(command: str) -> str:
        """An arbitrary parent execution tool."""
        return command

    graph = create_agent(
        _ToolCallingModel(
            responses=[
                _javascript_call(
                    "const allowed = await tools.readFile({file_path: '/permitted.txt'}); "
                    "const denied = await tools.readFile({file_path: '/../unmounted-secret.txt'}); "
                    "const paths = await tools.glob({pattern: '*.txt', path: '/'}); "
                    "const matches = await tools.grep({pattern: 'selected bridge', path: '/', "
                    "output_mode: 'content'}); JSON.stringify({allowed, denied, paths, matches, "
                    "write: typeof tools.writeFile, edit: typeof tools.editFile, "
                    "execute: typeof tools.execute})",
                    "javascript-read-scoped",
                ),
                AIMessage(content="complete"),
            ]
        ),
        tools=[execute],
        middleware=[
            FilesystemMiddleware(backend=FilesystemBackend(mounted_root, virtual_mode=True)),
            SchemaCompactionMiddleware(),
            _create_javascript_middleware(),
        ],
    )

    result = await graph.ainvoke({"messages": [HumanMessage(content="inspect files")]})
    outcome = json.loads(
        _tool_results(result)[0].removeprefix("<result>").removesuffix("</result>")
    )

    assert "visible through the selected bridge" in outcome["allowed"]
    assert "/permitted.txt" in outcome["paths"]
    assert "selected bridge" in outcome["matches"]
    assert "must not cross the selected bridge" not in outcome["denied"]
    assert outcome["write"] == "undefined"
    assert outcome["edit"] == "undefined"
    assert outcome["execute"] == "undefined"


async def test_javascript_a2a_bridge_uses_the_system_tool_adapter() -> None:
    with _deterministic_a2a_service() as (service, received):
        state = _A2AState()
        registry = _A2ARegistry(f"http://127.0.0.1:{service.server_port}")
        system_tools = create_deepagent_tools(tool_registry=registry, state_manager=state)
        tools = [
            tool for tool in system_tools if tool.name in {"a2a_send", "a2a_status", "a2a_list"}
        ]
        graph = _graph_for(
            "const sent = JSON.parse(await tools.a2aSend({agent: 'deterministic-a2a', "
            "message: 'bridge payload', wait_seconds: 1})); "
            "const status = JSON.parse(await tools.a2aStatus({task_id: sent.task_id})); "
            "const listed = JSON.parse(await tools.a2aList({agent: 'deterministic-a2a'})); "
            "JSON.stringify({sent, status, listed})",
            tools=tools,
        )

        result = await graph.ainvoke({"messages": [HumanMessage(content="send A2A work")]})

    outcome = _tool_results(result)[0]
    assert received == ["bridge payload"]
    assert '"task_id":"task-local"' in outcome
    assert '"status":"completed"' in outcome
    assert state.records["task-local"].latest_message == "remote: received"


async def test_javascript_enforces_ptc_read_call_budget(tmp_path: Path) -> None:
    (tmp_path / "permitted.txt").write_text("budget fixture")
    graph = create_agent(
        _ToolCallingModel(
            responses=[
                _javascript_call(
                    "await Promise.all(Array.from({length: 257}, "
                    "() => tools.readFile({file_path: '/permitted.txt'})))",
                    "javascript-ptc-budget",
                ),
                AIMessage(content="complete"),
            ]
        ),
        tools=[],
        middleware=[
            FilesystemMiddleware(backend=FilesystemBackend(tmp_path, virtual_mode=True)),
            _create_javascript_middleware(),
        ],
    )

    result = await graph.ainvoke({"messages": [HumanMessage(content="exercise PTC budget")]})

    assert 'type="PTCCallBudgetExceeded"' in _tool_results(result)[0]
