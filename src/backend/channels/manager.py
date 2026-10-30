"""Run channel adapters from ``channel:<id>`` documents and publish their state.

The manager is the only owner of running adapters. It reconciles on startup,
on every committed ``channel:`` document change (whoever wrote it: the
browser, a workflow or another backend), and on an explicit reconnect. The
browser sees channels only through ``channels.snapshot`` events: the stored
documents merged with live connection state and per-process message counts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from src.backend.channels.base import (
    CHANNEL_ENV_VAR_PREFIX,
    AdapterStatus,
    ChannelAdapter,
    ChannelError,
)
from src.backend.channels.credentials import database_storage_available, resolve_secret
from src.backend.channels.registry import CHANNEL_TYPES
from src.backend.channels.store import StoredChannel, load_channels, seed_channels
from src.backend.events.contracts import UiEvent
from src.backend.http.requests import BroadcastUiEvent
from src.backend.storage.documents import BackendDocumentStore
from src.progena.channels.contracts import (
    CredentialSource,
    channel_id_from_key,
    public_credential,
)
from src.progena.documents.kinds import CHANNEL_KEY_PREFIX
from src.progena.utils import utcnow_isoformat

logger = logging.getLogger(__name__)

TEST_TIMEOUT_SECONDS = 60.0

# Runtime events that should reach the user on a channel while no browser is open.
AWAY_EVENT_TYPES = frozenset(
    {UiEvent.SCHEDULED_RESPONSE, UiEvent.AGENT_MESSAGE, UiEvent.AGENT_QUESTION}
)


@dataclass
class ChannelStats:
    """Message counters since this backend process started the channel."""

    inbound: int = 0
    outbound: int = 0
    last_inbound_at: str | None = None
    last_outbound_at: str | None = None
    connected_at: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "inbound": self.inbound,
            "outbound": self.outbound,
            "last_inbound_at": self.last_inbound_at,
            "last_outbound_at": self.last_outbound_at,
            "connected_at": self.connected_at,
        }


@dataclass(frozen=True)
class _Desired:
    """A channel that should be running with these inputs."""

    adapter: type[ChannelAdapter]
    settings: dict[str, Any]
    secrets: dict[str, str]


def _fingerprint(stored: StoredChannel | None) -> str:
    if stored is None:
        return "deleted"
    document = stored.document if stored.document is not None else stored.raw
    if not isinstance(document, dict):
        return json.dumps(document, sort_keys=True, default=str)
    material = {key: document.get(key) for key in ("type", "enabled", "settings", "credentials")}
    return json.dumps(material, sort_keys=True, default=str)


def _desired_state(stored: StoredChannel) -> _Desired | AdapterStatus:
    """What should run for one stored channel, or why nothing can."""
    if stored.document is None:
        return AdapterStatus("error", f"Invalid channel document: {stored.error}")
    document = stored.document
    adapter = CHANNEL_TYPES.get(document["type"])
    if adapter is None:
        return AdapterStatus("unsupported", f"Unknown channel type {document['type']!r}")
    if not document["enabled"]:
        return AdapterStatus("disabled", "Channel is turned off")
    spec = adapter.spec
    try:
        settings = spec.validate_settings(document["settings"])
    except ValueError as exc:
        return AdapterStatus("error", f"Invalid settings: {exc}")
    missing = spec.missing_requirements(settings)
    if missing:
        return AdapterStatus("unconfigured", f"Required: {', '.join(missing)}")
    secrets: dict[str, str] = {}
    for field in spec.credentials:
        reference = document["credentials"].get(field.key)
        try:
            secret = resolve_secret(reference)
        except ValueError as exc:
            return AdapterStatus("error", f"{field.label}: {exc}")
        if secret is None:
            public = public_credential(reference)
            if public["source"] == CredentialSource.ENVIRONMENT:
                detail = f"{field.label}: environment variable {public['env_var']} is not set"
            else:
                detail = f"{field.label} is not configured"
            return AdapterStatus("unconfigured", detail)
        secrets[field.key] = secret
    return _Desired(adapter, settings, secrets)


class ChannelManager:
    """Owns running channel adapters; also the :class:`ChannelHost` they use."""

    def __init__(
        self,
        *,
        store_getter: Callable[[], BackendDocumentStore],
        runtime_client_getter: Callable[[], Any | None],
        broadcast: BroadcastUiEvent,
        has_ui_subscribers: Callable[[], bool],
    ) -> None:
        self._store_getter = store_getter
        self._runtime_client_getter = runtime_client_getter
        self._broadcast = broadcast
        self._has_ui_subscribers = has_ui_subscribers
        self._adapters: dict[str, ChannelAdapter] = {}
        self._inactive: dict[str, AdapterStatus] = {}
        self._fingerprints: dict[str, str] = {}
        self._connecting: set[str] = set()
        self._stats: dict[str, ChannelStats] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._broadcast_task: asyncio.Task[Any] | None = None
        self._broadcast_again = False
        self._running = False

    # -- ChannelHost --------------------------------------------------------

    def runtime_client(self) -> Any | None:
        return self._runtime_client_getter()

    def document_store(self) -> BackendDocumentStore:
        return self._store_getter()

    def status_changed(self, channel_id: str) -> None:
        logger.info("Channel %s connection state changed", channel_id)
        self.schedule_broadcast()

    def record_message(self, channel_id: str, direction: Literal["inbound", "outbound"]) -> None:
        stats = self._stats.setdefault(channel_id, ChannelStats())
        now = utcnow_isoformat()
        if direction == "inbound":
            stats.inbound += 1
            stats.last_inbound_at = now
        else:
            stats.outbound += 1
            stats.last_outbound_at = now
        self.schedule_broadcast()

    def spawn(self, coroutine: Awaitable[Any]) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coroutine)  # type: ignore[arg-type]
        except RuntimeError:
            close = getattr(coroutine, "close", None)
            if close is not None:
                close()
            logger.debug("No running loop; dropped a channel background task")
            return
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Channel background task failed", exc_info=task.exception())

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Seed missing channel documents, then start every enabled channel."""
        self._running = True
        try:
            created = await asyncio.to_thread(seed_channels, self._store_getter())
        except Exception:
            logger.exception("Failed to seed channel documents")
        else:
            if created:
                logger.info("Created channel documents: %s", ", ".join(created))
        self.schedule_reconcile()

    async def stop(self) -> None:
        self._running = False
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for channel_id in list(self._adapters):
            await self._stop_adapter(channel_id)
        self._inactive.clear()
        self._fingerprints.clear()
        self._connecting.clear()

    def on_store_change(self, _snapshot: Any, changed_key: str | None = None) -> None:
        """State-reader watcher: reconcile after a ``channel:`` document commit."""
        if changed_key is None:
            self.schedule_reconcile()
        elif changed_key.startswith(CHANNEL_KEY_PREFIX):
            channel_id = channel_id_from_key(changed_key)
            if channel_id is not None:
                self.schedule_reconcile(channel_id)

    def schedule_reconcile(self, channel_id: str | None = None, *, force: bool = False) -> None:
        if self._running:
            self.spawn(self.reconcile(channel_id, force=force))

    async def reconcile(self, channel_id: str | None = None, *, force: bool = False) -> None:
        """Make running adapters match the stored documents."""
        stored = await asyncio.to_thread(load_channels, self._store_getter())
        if channel_id is None:
            channel_ids = set(stored) | set(self._adapters) | set(self._inactive)
        else:
            channel_ids = {channel_id}
        for item in sorted(channel_ids):
            await self._reconcile_one(item, stored.get(item), force=force)
        self.schedule_broadcast()

    def _lock(self, channel_id: str) -> asyncio.Lock:
        return self._locks.setdefault(channel_id, asyncio.Lock())

    async def _reconcile_one(
        self, channel_id: str, stored: StoredChannel | None, *, force: bool
    ) -> None:
        async with self._lock(channel_id):
            try:
                await self._apply(channel_id, stored, force=force)
            finally:
                # Also ends a "connecting" shown by request_reconnect() when
                # nothing needed to change.
                self._connecting.discard(channel_id)

    async def _apply(self, channel_id: str, stored: StoredChannel | None, *, force: bool) -> None:
        fingerprint = _fingerprint(stored)
        if not force and self._fingerprints.get(channel_id) == fingerprint:
            return
        await self._stop_adapter(channel_id)
        self._inactive.pop(channel_id, None)
        if stored is None:
            self._fingerprints.pop(channel_id, None)
            self._stats.pop(channel_id, None)
            return
        self._fingerprints[channel_id] = fingerprint
        desired = _desired_state(stored)
        if isinstance(desired, AdapterStatus):
            self._inactive[channel_id] = desired
            return
        if not self._running:
            return
        adapter = desired.adapter(channel_id, desired.settings, desired.secrets, self)
        self._adapters[channel_id] = adapter
        self._connecting.add(channel_id)
        self.schedule_broadcast()
        try:
            await adapter.start()
        except Exception as exc:
            logger.exception("Channel %s failed to start", channel_id)
            self._adapters.pop(channel_id, None)
            try:
                await adapter.stop()
            except Exception:
                logger.debug("Channel %s cleanup failed", channel_id, exc_info=True)
            self._inactive[channel_id] = AdapterStatus("error", f"Failed to start: {exc}")
        else:
            self._stats.setdefault(channel_id, ChannelStats()).connected_at = utcnow_isoformat()
            logger.info("Channel %s started: %s", channel_id, adapter.status().state)

    async def _stop_adapter(self, channel_id: str) -> None:
        adapter = self._adapters.pop(channel_id, None)
        if adapter is None:
            return
        try:
            await adapter.stop()
        except Exception:
            logger.warning("Channel %s did not stop cleanly", channel_id, exc_info=True)
        stats = self._stats.get(channel_id)
        if stats is not None:
            stats.connected_at = None

    def request_reconnect(self, channel_id: str, *, force: bool = False) -> None:
        """Reconcile one channel soon, showing it as connecting until done.

        ``force`` restarts the adapter even when its document is unchanged.
        """
        if not self._running:
            return
        self._connecting.add(channel_id)
        self.schedule_reconcile(channel_id, force=force)

    # -- operations ---------------------------------------------------------

    def status(self, channel_id: str, document: dict[str, Any] | None) -> AdapterStatus:
        if document is not None and not document.get("enabled"):
            return AdapterStatus("disabled", "Channel is turned off")
        adapter = self._adapters.get(channel_id)
        if channel_id in self._connecting:
            handle = adapter.status().handle if adapter is not None else None
            return AdapterStatus("connecting", "Connecting…", handle)
        if adapter is not None:
            return adapter.status()
        inactive = self._inactive.get(channel_id)
        if inactive is not None:
            return inactive
        return AdapterStatus("disconnected", "Not started")

    async def send_test(self, channel_id: str, document: dict[str, Any] | None = None) -> None:
        adapter = self._adapters.get(channel_id)
        if adapter is None or channel_id in self._connecting:
            status = self.status(channel_id, document)
            raise ChannelError(status.detail or "Channel is not running")
        try:
            await asyncio.wait_for(adapter.send_test(), timeout=TEST_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise ChannelError("Timed out sending the test message") from exc

    def notify_away(self, event_type: str, content: str) -> None:
        """Forward an agent message to channels while no browser is connected."""
        if event_type not in AWAY_EVENT_TYPES or not content or self._has_ui_subscribers():
            return
        for adapter in list(self._adapters.values()):
            if adapter.notifies_when_away():
                self.spawn(adapter.notify(content, event_type))

    # -- snapshots ----------------------------------------------------------

    def _public_channel(self, stored: StoredChannel) -> dict[str, Any]:
        raw = stored.raw if isinstance(stored.raw, dict) else {}
        document = stored.document or raw
        adapter = CHANNEL_TYPES.get(str(document.get("type") or ""))
        spec = adapter.spec if adapter is not None else None
        status = self.status(stored.id, stored.document)
        if stored.document is None:
            status = AdapterStatus("error", f"Invalid channel document: {stored.error}")
        stored_settings = document.get("settings")
        stored_settings = stored_settings if isinstance(stored_settings, dict) else {}
        stored_credentials = document.get("credentials")
        stored_credentials = stored_credentials if isinstance(stored_credentials, dict) else {}
        if spec is not None:
            settings = {
                **(defaults := spec.default_settings()),
                **{key: value for key, value in stored_settings.items() if key in defaults},
            }
            fields = [item.public() for item in spec.fields]
            credentials = [
                {
                    "key": item.key,
                    "label": item.label,
                    "help": item.help,
                    "default_env_var": item.default_env_var,
                    "env_var_prefix": CHANNEL_ENV_VAR_PREFIX,
                    **public_credential(stored_credentials.get(item.key)),
                }
                for item in spec.credentials
            ]
        else:
            settings, fields, credentials = {}, [], []
        return {
            "id": stored.id,
            "type": document.get("type"),
            "name": document.get("name") or (spec.name if spec else stored.id),
            "category": spec.category if spec else "Other",
            "description": spec.description if spec else "",
            "enabled": bool(document.get("enabled")),
            "status": status.state,
            "status_detail": status.detail,
            "handle": status.handle,
            "session_source": spec.session_source if spec else None,
            "capabilities": (
                spec.capabilities.public()
                if spec
                else dict.fromkeys(
                    ("inbound", "outbound", "streaming", "threads", "attachments"), False
                )
            ),
            "settings": settings,
            "fields": fields,
            "credentials": credentials,
            "stats": self._stats.get(stored.id, ChannelStats()).public(),
            "updated_at": document.get("updated_at"),
        }

    async def snapshot_payload(self) -> dict[str, Any]:
        stored = await asyncio.to_thread(load_channels, self._store_getter())
        channels = [self._public_channel(item) for item in stored.values()]
        channels.sort(key=lambda channel: (str(channel["name"]).lower(), channel["id"]))
        return {
            "channels": channels,
            "credential_storage": {CredentialSource.DATABASE.value: database_storage_available()},
        }

    async def broadcast_snapshot(self, *, request_id: str | None = None) -> None:
        payload = await self.snapshot_payload()
        self._broadcast(UiEvent.CHANNELS_SNAPSHOT, payload, request_id=request_id)

    def schedule_broadcast(self) -> None:
        """Publish one unsolicited snapshot soon; bursts coalesce."""
        if self._broadcast_task is not None and not self._broadcast_task.done():
            self._broadcast_again = True
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._broadcast_task = loop.create_task(self._broadcast_unsolicited())

    async def _broadcast_unsolicited(self) -> None:
        while True:
            self._broadcast_again = False
            try:
                await self.broadcast_snapshot()
            except Exception:
                logger.debug("Failed to broadcast channels snapshot", exc_info=True)
            if not self._broadcast_again:
                return


_manager: ChannelManager | None = None


def get_channel_manager() -> ChannelManager:
    """The backend's channel manager, wired to the default store and stream."""
    global _manager
    if _manager is None:
        import src.backend.events.stream as events_stream
        import src.backend.lifecycle.state as lifecycle_state
        import src.backend.storage.documents as storage_documents

        _manager = ChannelManager(
            store_getter=storage_documents.get_default_document_store,
            runtime_client_getter=lambda: lifecycle_state.client,
            broadcast=events_stream.broadcast_ui_event,
            has_ui_subscribers=events_stream.has_active_ui_stream_subscribers,
        )
    return _manager
