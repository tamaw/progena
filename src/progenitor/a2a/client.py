"""Client for calling external A2A agents."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types import (
    Message,
    MessageSendConfiguration,
    Part,
    PushNotificationConfig,
    Role,
    TaskIdParams,
    TaskQueryParams,
    TextPart,
)

from ..utils import utcnow_isoformat

logger = logging.getLogger(__name__)
_TERMINAL_TASK_STATES = {"completed", "failed", "canceled", "cancelled"}
_SETTLED_TASK_STATES = _TERMINAL_TASK_STATES | {"input-required"}

_MAX_PROGRESS_UPDATES = 50


class A2AClient:
    """Client for calling A2A agents over HTTP."""

    def _resolve_agent_url(self, agent_config: dict[str, Any]) -> str:
        url = str(agent_config.get("url") or "").strip()
        if not url:
            raise ValueError("No agent URL configured")
        return url

    def _build_headers(
        self,
        agent_config: dict[str, Any],
        *,
        include_content_type: bool = True,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        custom_headers = agent_config.get("headers")
        if isinstance(custom_headers, dict):
            headers.update({str(key): str(value) for key, value in custom_headers.items()})
        return headers

    async def _close_client(self, client: Any) -> None:
        close = getattr(client, "close", None)
        if not callable(close):
            return
        maybe = close()
        if inspect.isawaitable(maybe):
            await cast(Any, maybe)

    @asynccontextmanager
    async def _sdk_client(
        self,
        agent_config: dict[str, Any],
        *,
        timeout: float,
        streaming: bool = False,
        polling: bool = False,
    ):
        url = self._resolve_agent_url(agent_config)
        headers = self._build_headers(agent_config, include_content_type=False)
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as httpx_client:
            client_config = ClientConfig(
                streaming=streaming,
                polling=polling,
                httpx_client=httpx_client,
            )
            client = await cast(Any, ClientFactory).connect(
                url,
                client_config=client_config,
                resolver_http_kwargs={"headers": headers},
            )
            try:
                yield cast(Any, client)
            finally:
                await self._close_client(client)

    @staticmethod
    def _serialize_model(payload: Any) -> Any:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json", by_alias=True, exclude_none=True)
        return payload

    @staticmethod
    def _task_state(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        status = payload.get("status")
        if not isinstance(status, dict):
            return ""
        return str(status.get("state") or "").strip().lower()

    async def _await_terminal_task(
        self,
        agent_config: dict[str, Any],
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        state = self._task_state(payload)
        if state in _SETTLED_TASK_STATES:
            return payload

        task_id = str(payload.get("id") or "").strip()
        if not task_id:
            return payload

        if deadline is None:
            deadline = time.monotonic() + max(timeout or 0.0, 0.0)
        latest = payload
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return latest
            try:
                async with asyncio.timeout(remaining):
                    polled = await self.get_task(
                        agent_config,
                        task_id,
                        history_length=None,
                        timeout=min(remaining, 15.0),
                    )
            except (TimeoutError, httpx.TimeoutException):
                return latest
            if not polled.get("success"):
                return latest
            result = polled.get("result")
            if not isinstance(result, dict):
                return latest
            latest = result
            if self._task_state(latest) in _SETTLED_TASK_STATES:
                return latest
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return latest
            await asyncio.sleep(min(0.25, remaining))

    async def send_message(
        self,
        agent_config: dict[str, Any],
        request: str,
        context: dict[str, Any] | None = None,
        timeout: float = 60.0,
        blocking: bool = False,
        history_length: int | None = None,
        callback_url: str | None = None,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message to an A2A agent, optionally continuing a task or context."""
        transport = agent_config.get("transport", "http")
        status = agent_config.get("status", "stopped")
        if status == "failed":
            return {
                "success": False,
                "error": f"Agent is in failed state: {agent_config.get('last_error', 'Unknown error')}",
            }

        if transport != "http":
            return {
                "success": False,
                "error": f"Unsupported transport: {transport}. Only 'http' is supported.",
            }

        deadline = time.monotonic() + max(timeout, 0.0)
        last_payload: Any = None
        progress_updates: deque[Any] = deque(maxlen=_MAX_PROGRESS_UPDATES)

        try:
            remaining = max(deadline - time.monotonic(), 0.0)
            async with (
                asyncio.timeout(remaining),
                self._sdk_client(
                    agent_config,
                    timeout=remaining,
                    # Only request streaming when a caller actually wants
                    # intermediate progress (on_progress): callers that don't
                    # are better served by plain non-streaming request/response,
                    # which doesn't depend on the target agent's SSE support at
                    # all. Requesting it unconditionally would be *safe*
                    # regardless of what the agent advertises -- the SDK
                    # negotiates against the agent card (base_client.py checks
                    # self._config.streaming AND card.capabilities.streaming)
                    # and falls back transparently -- but "safe" isn't the same
                    # as "no reason not to": narrowing the blast radius to only
                    # the callers who opted in is strictly better.
                    streaming=on_progress is not None,
                    polling=not blocking,
                ) as client,
            ):
                message = Message(
                    role=cast(Any, getattr(Role, "user", "user")),
                    parts=[Part(root=TextPart(text=request or ""))],
                    message_id=uuid.uuid4().hex,
                    metadata=context or {},
                    task_id=task_id,
                    context_id=context_id,
                    reference_task_ids=[task_id] if task_id else None,
                )
                configuration = MessageSendConfiguration(
                    blocking=blocking,
                    history_length=history_length,
                    push_notification_config=(
                        PushNotificationConfig(url=callback_url) if callback_url else None
                    ),
                )
                stream = client.send_message(message, configuration=configuration)
                try:
                    # httpx's per-request timeout bounds the gap between
                    # events, not the stream as a whole. Use the same absolute
                    # deadline that terminal polling below also observes.
                    remaining = max(deadline - time.monotonic(), 0.0)
                    async with asyncio.timeout(remaining):
                        async for item in stream:
                            if isinstance(item, tuple) and len(item) == 2:
                                task_payload = self._serialize_model(item[0])
                                update_payload = (
                                    self._serialize_model(item[1]) if item[1] is not None else None
                                )
                                if isinstance(task_payload, dict) and update_payload is not None:
                                    if isinstance(update_payload, dict):
                                        # Stamped at the moment this update actually
                                        # arrived -- not part of the A2A wire spec,
                                        # purely internal bookkeeping for progress log
                                        # extraction.
                                        update_payload["_progenitor_received_at"] = (
                                            utcnow_isoformat()
                                        )
                                    task_payload["latestUpdate"] = update_payload
                                    # Publish the latest task snapshot before invoking
                                    # user code, so a callback timeout cannot discard
                                    # the remote task ID/state that just arrived.
                                    last_payload = task_payload
                                    # Artifact updates carry result data, not status
                                    # progress. Keep them on the task snapshot but do
                                    # not retain or forward them as progress events.
                                    is_artifact_update = (
                                        isinstance(update_payload, dict)
                                        and str(update_payload.get("kind") or "")
                                        .strip()
                                        .lower()
                                        .replace("_", "-")
                                        == "artifact-update"
                                    )
                                    if blocking and not is_artifact_update:
                                        progress_updates.append(update_payload)
                                        if on_progress is not None:
                                            # Best-effort: a UI-emission failure
                                            # must never break the actual A2A call.
                                            try:
                                                await on_progress(update_payload)
                                            except Exception:
                                                logger.debug(
                                                    "on_progress callback failed", exc_info=True
                                                )
                                else:
                                    last_payload = task_payload
                            else:
                                last_payload = self._serialize_model(item)
                            if not blocking:
                                # Non-blocking dispatch: take the first snapshot
                                # and stop. Live progress after this point belongs
                                # to a separate tasks/resubscribe call.
                                break
                finally:
                    aclose = getattr(stream, "aclose", None)
                    if callable(aclose):
                        with contextlib.suppress(Exception):
                            maybe = aclose()
                            if inspect.isawaitable(maybe):
                                remaining = max(deadline - time.monotonic(), 0.0)
                                async with asyncio.timeout(remaining):
                                    await cast(Any, maybe)
        except (TimeoutError, httpx.TimeoutException):
            # A blocking stream may have already yielded a live task snapshot.
            # Return it with the timeout so callers can persist the remote task
            # ID/state and resume polling instead of losing the still-running task.
            result: dict[str, Any] = {
                "success": False,
                "error": f"Request timeout after {timeout}s",
            }
            if isinstance(last_payload, dict):
                result["result"] = last_payload
            if progress_updates:
                result["progress_updates"] = list(progress_updates)
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        if last_payload is None:
            return {"success": False, "error": "Agent returned no response"}
        if blocking and isinstance(last_payload, dict):
            last_payload = await self._await_terminal_task(
                agent_config,
                last_payload,
                deadline=deadline,
            )
        result = {"success": True, "result": last_payload}
        if progress_updates:
            # Keep only the bounded recent status-update trail. Also live-stream
            # those same updates to the chat UI via on_progress as they arrive.
            result["progress_updates"] = list(progress_updates)
        return result

    async def get_task(
        self,
        agent_config: dict[str, Any],
        task_id: str,
        history_length: int | None = 20,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Fetch an A2A task snapshot by task ID."""
        try:
            async with self._sdk_client(agent_config, timeout=timeout) as client:
                task = await client.get_task(
                    TaskQueryParams(id=task_id, history_length=history_length)
                )
        except (TimeoutError, httpx.TimeoutException):
            return {"success": False, "error": f"Request timeout after {timeout}s"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "result": self._serialize_model(task)}

    async def cancel_task(
        self,
        agent_config: dict[str, Any],
        task_id: str,
        *,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Cancel a remote A2A task and return its latest snapshot."""
        try:
            async with self._sdk_client(agent_config, timeout=timeout) as client:
                task = await client.cancel_task(TaskIdParams(id=task_id))
        except (TimeoutError, httpx.TimeoutException):
            return {"success": False, "error": f"Request timeout after {timeout}s"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "result": self._serialize_model(task)}

    async def get_agent_card(
        self,
        agent_config: dict[str, Any],
        timeout: float = 5.0,
    ) -> dict[str, Any] | None:
        """Fetch the agent card from an HTTP agent."""
        agent_card_url = agent_config.get("agent_card_url")
        if not agent_card_url:
            url = str(agent_config.get("url") or "").strip()
            if not url:
                return None
            agent_card_url = f"{url}/.well-known/agent-card.json"

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=self._build_headers(agent_config, include_content_type=False),
            ) as client:
                response = await client.get(str(agent_card_url))
                response.raise_for_status()
                return cast(dict[str, Any], response.json())
        except Exception as exc:
            logger.debug("Failed to fetch agent card: %s", exc)
            return None
