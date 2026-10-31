"""Persistent HTTP runtime endpoints for Progena."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any, cast

from aiohttp import web

from src.progena.a2a.models import A2ATaskRecord
from src.progena.a2a.payloads import build_task_response, is_task_payload
from src.progena.a2a.tasks import upsert_tracked_task_from_payload
from src.progena.async_subagents.hello_world import hello_world_runnable
from src.progena.async_subagents.protocol import ObjectAgentProtocol
from src.progena.async_subagents.tasks import HELLO_WORLD_ASYNC_ASSISTANT_ID
from src.progena.routines.controls import RoutineRunControlConflict
from src.progena.runtime.http.chat import RuntimeChatService
from src.progena.utils import utcnow_isoformat

logger = logging.getLogger(__name__)


class ProgenaHTTPServer:
    """Startup-managed HTTP server for health, agent card, and A2A callbacks."""

    def __init__(
        self,
        *,
        state_manager: Any,
        loop_runner: Any = None,
        tool_registry: Any = None,
        routine_controls: Any = None,
        host: str = "127.0.0.1",
        port: int = 9501,
        public_base_url: str | None = None,
    ) -> None:
        self.state_manager = state_manager
        self.loop_runner = loop_runner
        self.tool_registry = tool_registry
        self.host = host
        self.port = port
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None

        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._bound_port: int | None = None
        self.routine_controls = routine_controls
        store = getattr(state_manager, "_runtime_store", None)
        self._chat_service = RuntimeChatService(loop_runner) if loop_runner is not None else None
        conversation_gate = (
            self._chat_service.conversation_gate if self._chat_service is not None else None
        )
        self._agent_protocol = (
            ObjectAgentProtocol(
                store=store,
                resolve_agent=self._resolve_object_agent,
                conversation_gate=conversation_gate,
            )
            if store is not None
            else None
        )
        heartbeat_context = getattr(loop_runner, "heartbeat_context", None)
        if heartbeat_context is not None and self._agent_protocol is not None:
            heartbeat_context.object_agent_protocol = self._agent_protocol

    @property
    def bound_port(self) -> int | None:
        """Return the actual bound port, useful when configured with port 0."""
        return self._bound_port

    def _resolve_object_agent(self, name: str) -> Any | None:
        if name == HELLO_WORLD_ASYNC_ASSISTANT_ID:
            return hello_world_runnable()
        graph = getattr(self.loop_runner, "graph", None)
        registry = getattr(graph, "object_agent_registry", None)
        if registry is None:
            registry = getattr(getattr(graph, "agent", None), "object_agent_registry", None)
        return registry.callables.get(name) if registry is not None else None

    @property
    def base_url(self) -> str:
        """Return the advertised base URL for this runtime HTTP service."""
        if self.public_base_url:
            return self.public_base_url
        host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host  # nosec B104
        return f"http://{host}:{self._bound_port or self.port}"

    async def start(self) -> None:
        """Start listening for HTTP requests."""
        if self._runner is not None:
            return

        app = web.Application()
        app.add_routes(
            [
                web.get("/health", self._handle_health),
                web.get("/.well-known/agent-card.json", self._handle_agent_card),
                web.post("/a2a/tasks/callback", self._handle_a2a_task_callback),
                *(self._agent_protocol.routes() if self._agent_protocol is not None else []),
                web.post("/v1/routine-runs/control", self._handle_routine_run_control),
                *(self._chat_service.routes() if self._chat_service is not None else []),
            ]
        )
        self._app = app
        if self._agent_protocol is not None:
            await self._agent_protocol.start()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        if self._chat_service is not None:
            await self._chat_service.start()
        self._site = web.TCPSite(self._runner, self.host, self.port, reuse_address=True)
        await self._site.start()

        server = getattr(self._site, "_server", None)
        sockets = getattr(server, "sockets", None)
        if sockets:
            self._bound_port = int(sockets[0].getsockname()[1])
        else:
            self._bound_port = self.port
        logger.info("Progena HTTP server listening on %s", self.base_url)

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner is None:
            return
        if self._agent_protocol is not None:
            await self._agent_protocol.stop()
        if self._chat_service is not None:
            await self._chat_service.stop()
        await self._runner.cleanup()
        self._site = None
        self._runner = None
        self._app = None
        self._bound_port = None
        logger.info("Progena HTTP server stopped")

    async def _handle_health(self, _request: web.Request) -> web.Response:
        state: dict[str, Any] = {}
        get_current_state = getattr(self.state_manager, "get_current_state", None)
        if callable(get_current_state):
            try:
                maybe_state = get_current_state()
                loaded = (
                    await cast(Awaitable[Any], maybe_state)
                    if isawaitable(maybe_state)
                    else maybe_state
                )
                if isinstance(loaded, dict):
                    state = loaded
            except Exception as exc:
                logger.debug("Health state lookup failed: %s", exc)

        queue_stats = None
        queues = getattr(self.loop_runner, "queues", None) if self.loop_runner is not None else None
        if queues is not None:
            try:
                queue_stats = queues.get_queue_stats()
            except Exception as exc:
                logger.debug("Health queue lookup failed: %s", exc)

        payload: dict[str, Any] = {
            "status": "ok",
            "service": "progena",
            "timestamp": utcnow_isoformat(),
            "thread_id": state.get("thread_id"),
            "cycle_count": state.get("cycle_count", 0),
        }
        if queue_stats is not None:
            payload["queues"] = queue_stats
        return web.json_response(payload)

    async def _handle_agent_card(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "name": "progena",
                "description": "Conversational orchestrator for user chat, schedules, and external A2A delegation.",
                "url": self.base_url,
                "version": "0.1.0",
                "capabilities": [
                    "conversation",
                    "scheduling",
                    "a2a_delegation",
                    "a2a_task_callbacks",
                ],
                "endpoints": {
                    "health": f"{self.base_url}/health",
                    "a2a_task_callback": f"{self.base_url}/a2a/tasks/callback",
                },
            }
        )

    def _resolve_routine_controls(self) -> Any | None:
        if self.routine_controls is not None:
            return self.routine_controls
        graph = getattr(self.loop_runner, "graph", None)
        controls = getattr(graph, "routine_controls", None)
        if controls is None:
            controls = getattr(getattr(graph, "agent", None), "routine_controls", None)
        return controls

    async def _handle_routine_run_control(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON payload"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "Expected JSON object"}, status=400)
        controls = self._resolve_routine_controls()
        controller = getattr(controls, "control_routine_run", None)
        if not callable(controller):
            return web.json_response({"error": "Routine run controls are unavailable"}, status=409)
        try:
            control = cast(Callable[..., Awaitable[dict[str, Any]]], controller)
            result = await control(
                payload.get("agent_id"),
                thread_id=payload.get("thread_id"),
                request_id=payload.get("request_id"),
                action=payload.get("action"),
            )
        except RoutineRunControlConflict as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)

    async def _handle_a2a_task_callback(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response(
                {"success": False, "error": "Invalid JSON payload"}, status=400
            )

        if not isinstance(payload, dict):
            return web.json_response(
                {"success": False, "error": "Expected JSON object"}, status=400
            )

        task_payload = payload.get("task") if isinstance(payload.get("task"), dict) else payload
        if not is_task_payload(task_payload):
            return web.json_response(
                {"success": False, "error": "Payload does not contain a serialized A2A task"},
                status=400,
            )

        task_id = str(cast(dict[str, Any], task_payload).get("id") or "").strip()
        existing = await self.state_manager.get_a2a_task(task_id)
        agent_name = self._resolve_callback_agent_name(request, payload, existing)
        if not agent_name:
            return web.json_response(
                {
                    "success": False,
                    "error": "Missing agent name for unknown A2A task callback",
                },
                status=400,
            )

        agent_config = self._resolve_callback_agent_config(agent_name, existing)
        request_text = str(
            payload.get("request_text") or getattr(existing, "request_text", "") or ""
        )
        metadata = dict(getattr(existing, "request_metadata", {}) or {})
        callback_metadata = payload.get("metadata")
        if isinstance(callback_metadata, dict):
            metadata.update(callback_metadata)
        metadata["callback_received_at"] = utcnow_isoformat()

        record = await upsert_tracked_task_from_payload(
            self.state_manager,
            task_payload=cast(dict[str, Any], task_payload),
            agent_name=agent_name,
            agent_config=agent_config,
            source=str(getattr(existing, "source", "") or payload.get("source") or "a2a_callback"),
            request_text=request_text,
            request_metadata=metadata,
            notify_on_completion=bool(getattr(existing, "notify_on_completion", True)),
        )
        await self._record_terminal_callback(record)
        return web.json_response({"success": True, **build_task_response(record)})

    def _resolve_callback_agent_name(
        self,
        request: web.Request,
        payload: dict[str, Any],
        existing: A2ATaskRecord | None,
    ) -> str:
        candidates = [
            request.query.get("agent"),
            payload.get("agent"),
            payload.get("agent_name"),
            payload.get("name"),
            getattr(existing, "agent_name", None),
        ]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    def _resolve_callback_agent_config(
        self,
        agent_name: str,
        existing: A2ATaskRecord | None,
    ) -> dict[str, Any]:
        if self.tool_registry is not None and hasattr(self.tool_registry, "find_by_name"):
            try:
                cfg = self.tool_registry.find_by_name(agent_name)
                if isinstance(cfg, dict):
                    return dict(cfg)
            except Exception as exc:
                logger.debug("Callback registry lookup failed for %s: %s", agent_name, exc)
        return {
            "name": agent_name,
            "url": getattr(existing, "agent_url", None),
            "agent_card_url": getattr(existing, "agent_card_url", None),
            "transport": getattr(existing, "transport", "http"),
        }

    async def _record_terminal_callback(self, record: A2ATaskRecord) -> None:
        if not record.is_terminal or self.loop_runner is None:
            return
        queues = getattr(self.loop_runner, "queues", None)
        if queues is not None:
            queues.enqueue_a2a_completions([record])
