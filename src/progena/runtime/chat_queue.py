"""Durable, conversation-scoped chat input queue.

The queue lives outside LangGraph checkpoints so UI actions can mutate pending input
without racing a running graph.  A claimed input is only considered delivered after
its stable HumanMessage id is visible in the checkpoint; restart recovery uses that
fact instead of trusting an in-process claim.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from src.progena.documents.values import make_json_safe
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.utils import utcnow_isoformat

_QUEUE_PREFIX = "chat_queue:"
_COMMAND_HISTORY_LIMIT = 100


class QueueActionError(ValueError):
    """A user-action failure with an HTTP-safe status and optional snapshot."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.snapshot = snapshot


@dataclass(frozen=True)
class ClaimedQueueMessage:
    """A queue item reserved for insertion into a graph checkpoint."""

    conversation_id: str
    id: str
    text: str
    attachments: list[dict[str, Any]]
    metadata: dict[str, Any]
    source: str | None
    received_at: float | None
    response_id: str
    model: str
    send_now: bool


QueueEventPublisher = Callable[[dict[str, Any]], Awaitable[None]]


class ChatQueueStore:
    """Own the durable pending-input queue and its atomic transitions."""

    def __init__(self, runtime_store: LangGraphRuntimeStore) -> None:
        self._store = runtime_store
        self._publisher: QueueEventPublisher | None = None

    def set_event_publisher(self, publisher: QueueEventPublisher | None) -> None:
        self._publisher = publisher

    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"{_QUEUE_PREFIX}{conversation_id}"

    @staticmethod
    def _empty(conversation_id: str) -> dict[str, Any]:
        return {"conversation_id": conversation_id, "revision": 0, "messages": [], "commands": {}}

    @staticmethod
    def _snapshot(document: dict[str, Any]) -> dict[str, Any]:
        messages = document.get("messages")
        visible = []
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                item = {
                    key: make_json_safe(message[key])
                    for key in ("id", "text", "attachments", "status", "created_at", "request_id")
                    if key in message
                }
                if message.get("send_now_order") is not None:
                    item["send_now"] = True
                visible.append(item)
        return {
            "conversation_id": str(document.get("conversation_id") or ""),
            "revision": int(document.get("revision") or 0),
            "messages": visible,
        }

    async def _emit(self, snapshot: dict[str, Any]) -> None:
        if self._publisher is not None:
            await self._publisher(snapshot)

    async def snapshot(self, conversation_id: str) -> dict[str, Any]:
        document = await self._store.peek_document(self._key(conversation_id))
        if not isinstance(document, dict):
            return self._snapshot(self._empty(conversation_id))
        return self._snapshot(document)

    async def delete(self, conversation_id: str) -> None:
        """Remove all pending input for a deleted conversation."""
        await self._store.delete_document(self._key(conversation_id))

    async def enqueue(
        self,
        conversation_id: str,
        *,
        text: str,
        attachments: list[dict[str, Any]],
        metadata: dict[str, Any] | None,
        source: str | None,
        received_at: float,
        response_id: str,
        model: str,
    ) -> tuple[dict[str, Any], str]:
        """Append an input exactly once, using its response id as its stable identity."""

        message_id = f"user_{response_id}"

        def mutate(current: Any) -> tuple[dict[str, Any], tuple[dict[str, Any], str]]:
            document = dict(current) if isinstance(current, dict) else self._empty(conversation_id)
            messages = [
                dict(item) for item in document.get("messages", []) if isinstance(item, dict)
            ]
            for item in messages:
                if item.get("id") == message_id:
                    document["messages"] = messages
                    return document, (self._snapshot(document), message_id)
            messages.append(
                {
                    "id": message_id,
                    "text": text,
                    "attachments": make_json_safe(attachments),
                    "metadata": make_json_safe(metadata or {}),
                    "source": source,
                    "received_at": received_at,
                    "request_id": response_id,
                    "response_id": response_id,
                    "model": model,
                    "created_at": utcnow_isoformat(),
                    "status": "queued",
                    "send_now_order": None,
                }
            )
            document.update(
                {
                    "conversation_id": conversation_id,
                    "revision": int(document.get("revision") or 0) + 1,
                    "messages": messages,
                }
            )
            return document, (self._snapshot(document), message_id)

        snapshot, stable_id = await self._store.mutate_document(self._key(conversation_id), mutate)
        await self._emit(snapshot)
        return snapshot, stable_id

    async def action(self, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply an idempotent user queue action."""

        action = payload.get("action")
        command_id = payload.get("command_id")
        if action not in {"delete", "reorder", "send_now"}:
            raise QueueActionError(
                "bad_request", "action must be delete, reorder, or send_now", 400
            )
        if not isinstance(command_id, str) or not command_id.strip():
            raise QueueActionError("bad_request", "command_id is required", 400)
        command_id = command_id.strip()
        revision = payload.get("revision")
        if type(revision) is not int or revision < 0:
            raise QueueActionError("bad_request", "revision must be a non-negative integer", 400)

        def mutate(current: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            document = dict(current) if isinstance(current, dict) else self._empty(conversation_id)
            document["conversation_id"] = conversation_id
            commands = dict(document.get("commands") or {})
            previous = commands.get(command_id)
            if isinstance(previous, dict):
                return document, self._snapshot(document)
            snapshot = self._snapshot(document)
            if revision != snapshot["revision"]:
                raise QueueActionError(
                    "queue_revision_conflict",
                    "queue revision is stale",
                    409,
                    snapshot,
                )
            messages = [
                dict(item) for item in document.get("messages", []) if isinstance(item, dict)
            ]
            queued = [item for item in messages if item.get("status") == "queued"]
            message_id = payload.get("message_id")
            if action in {"delete", "send_now"}:
                if not isinstance(message_id, str) or not message_id:
                    raise QueueActionError("bad_request", "message_id is required", 400)
                target = next((item for item in messages if item.get("id") == message_id), None)
                if target is None:
                    raise QueueActionError("not_found", "queue message was not found", 404)
                if target.get("status") != "queued":
                    raise QueueActionError(
                        "queue_message_claimed",
                        "queue message is already sending",
                        409,
                        snapshot,
                    )
                if action == "delete":
                    messages = [item for item in messages if item.get("id") != message_id]
                else:
                    if target.get("send_now_order") is None:
                        next_order = int(document.get("next_send_now_order") or 0) + 1
                        target["send_now_order"] = next_order
                        document["next_send_now_order"] = next_order
            else:
                order = payload.get("order")
                if not isinstance(order, list) or not all(
                    isinstance(value, str) for value in order
                ):
                    raise QueueActionError(
                        "bad_request", "order must be an array of message ids", 400
                    )
                queued_ids = [str(item.get("id")) for item in queued]
                if len(order) != len(queued_ids) or set(order) != set(queued_ids):
                    raise QueueActionError(
                        "bad_request", "order must contain every queued message exactly once", 400
                    )
                by_id = {str(item["id"]): item for item in queued}
                reordered = [by_id[item_id] for item_id in order]
                iterator = iter(reordered)
                messages = [
                    next(iterator) if item.get("status") == "queued" else item for item in messages
                ]
            document["messages"] = messages
            document["revision"] = int(document.get("revision") or 0) + 1
            updated = self._snapshot(document)
            commands[command_id] = {"revision": updated["revision"]}
            if len(commands) > _COMMAND_HISTORY_LIMIT:
                commands = dict(list(commands.items())[-_COMMAND_HISTORY_LIMIT:])
            document["commands"] = commands
            return document, updated

        snapshot = await self._store.mutate_document(self._key(conversation_id), mutate)
        await self._emit(snapshot)
        return snapshot

    async def claim_next(
        self, conversation_id: str, *, immediate_only: bool = False
    ) -> ClaimedQueueMessage | None:
        """Atomically reserve the next selected input for a graph boundary."""
        current = await self._store.peek_document(self._key(conversation_id))
        if not isinstance(current, dict):
            return None
        messages = current.get("messages")
        if not isinstance(messages, list) or not any(
            isinstance(item, dict)
            and item.get("status") == "queued"
            and (not immediate_only or item.get("send_now_order") is not None)
            for item in messages
        ):
            return None

        def mutate(current: Any) -> tuple[dict[str, Any], ClaimedQueueMessage | None]:
            document = dict(current) if isinstance(current, dict) else self._empty(conversation_id)
            messages = [
                dict(item) for item in document.get("messages", []) if isinstance(item, dict)
            ]
            queued = [candidate for candidate in messages if candidate.get("status") == "queued"]
            immediate = [
                candidate for candidate in queued if candidate.get("send_now_order") is not None
            ]
            if immediate:
                item = min(immediate, key=lambda candidate: int(candidate["send_now_order"]))
            elif immediate_only:
                item = None
            else:
                item = queued[0] if queued else None
            if item is None:
                document["messages"] = messages
                return document, None
            item["status"] = "sending"
            document["messages"] = messages
            document["conversation_id"] = conversation_id
            document["revision"] = int(document.get("revision") or 0) + 1
            return document, ClaimedQueueMessage(
                conversation_id=conversation_id,
                id=str(item["id"]),
                text=str(item.get("text") or ""),
                attachments=list(item.get("attachments") or []),
                metadata=dict(item.get("metadata") or {}),
                source=str(item["source"]) if item.get("source") else None,
                received_at=(
                    float(item["received_at"])
                    if isinstance(item.get("received_at"), (int, float))
                    else None
                ),
                response_id=str(item.get("response_id") or item.get("request_id") or item["id"]),
                model=str(item.get("model") or "progena-v1"),
                send_now=item.get("send_now_order") is not None,
            )

        claimed = await self._store.mutate_document(self._key(conversation_id), mutate)
        if claimed is not None:
            await self._emit(await self.snapshot(conversation_id))
        return claimed

    async def recover(
        self, conversation_id: str, checkpoint_message_ids: Iterable[str]
    ) -> dict[str, Any]:
        """Reconcile incomplete claims with committed checkpoint messages."""

        committed = set(checkpoint_message_ids)
        current = await self._store.peek_document(self._key(conversation_id))
        if not isinstance(current, dict):
            return self._snapshot(self._empty(conversation_id))
        current_messages = current.get("messages")
        if not isinstance(current_messages, list) or not any(
            isinstance(item, dict) and item.get("status") == "sending" for item in current_messages
        ):
            return self._snapshot(current)

        def mutate(current: Any) -> tuple[dict[str, Any], tuple[dict[str, Any], bool]]:
            document = dict(current) if isinstance(current, dict) else self._empty(conversation_id)
            changed = False
            messages: list[dict[str, Any]] = []
            for item in document.get("messages", []):
                if not isinstance(item, dict):
                    changed = True
                    continue
                copy = dict(item)
                if copy.get("status") == "sending":
                    changed = True
                    if str(copy.get("id")) in committed:
                        continue
                    copy["status"] = "queued"
                messages.append(copy)
            if changed:
                document["messages"] = messages
                document["conversation_id"] = conversation_id
                document["revision"] = int(document.get("revision") or 0) + 1
            return document, (self._snapshot(document), changed)

        snapshot, changed = await self._store.mutate_document(self._key(conversation_id), mutate)
        if changed:
            await self._emit(snapshot)
        return snapshot

    async def discard_claim(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        """Settle a failed pre-checkpoint claim so later input can proceed."""

        def mutate(current: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            document = dict(current) if isinstance(current, dict) else self._empty(conversation_id)
            messages = [
                dict(item) for item in document.get("messages", []) if isinstance(item, dict)
            ]
            retained = [
                item
                for item in messages
                if not (item.get("id") == message_id and item.get("status") == "sending")
            ]
            if len(retained) != len(messages):
                document["messages"] = retained
                document["revision"] = int(document.get("revision") or 0) + 1
            return document, self._snapshot(document)

        snapshot = await self._store.mutate_document(self._key(conversation_id), mutate)
        await self._emit(snapshot)
        return snapshot

    async def conversation_ids(self) -> list[str]:
        rows = await self._store.list_documents(_QUEUE_PREFIX, payload_fields=("conversation_id",))
        return [
            str(payload.get("conversation_id"))
            for _key, payload in rows
            if isinstance(payload, dict) and isinstance(payload.get("conversation_id"), str)
        ]
