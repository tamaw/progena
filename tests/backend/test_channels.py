"""Channel documents, the channel manager and the acknowledged channel routes.

A fake channel type stands in for Telegram so these tests exercise the
document → adapter lifecycle without network access.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from ack_helpers import assert_acknowledged
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.backend.channels.base import (
    AdapterStatus,
    ChannelAdapter,
    ChannelCapabilities,
    ChannelError,
    ChannelTypeSpec,
    CredentialField,
    SettingField,
)
from src.backend.channels.credentials import resolve_secret
from src.backend.channels.manager import ChannelManager
from src.backend.channels.registry import CHANNEL_TYPES
from src.backend.channels.routes import create_channels_router
from src.backend.channels.store import load_channels, seed_channels, update_channel
from src.backend.channels.telegram.adapter import TelegramChannel
from src.backend.storage.documents import PROGENA_STATE_PREFIX, InMemoryDocumentStore
from src.progena.channels.contracts import redacted_channel_document

FAKE_SPEC = ChannelTypeSpec(
    type="fake",
    name="Fake",
    category="Testing",
    description="A channel used by tests.",
    session_source="ui",
    capabilities=ChannelCapabilities(
        inbound=True, outbound=True, streaming=False, threads=False, attachments=False
    ),
    fields=(
        SettingField("target", "Target", "text", "", required=True),
        SettingField("notify_when_away", "Notify when away", "boolean", True),
        SettingField("retries", "Retries", "integer", 3, minimum=1, maximum=5),
        SettingField("chats", "Chats", "list", []),
    ),
    credentials=(CredentialField("token", "Token", "FAKE_CHANNEL_TOKEN"),),
)


class FakeChannel(ChannelAdapter):
    spec = FAKE_SPEC
    instances: list[FakeChannel] = []
    fail_start = False

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.running = False
        self.stopped = False
        self.notified: list[tuple[str, str]] = []
        FakeChannel.instances.append(self)

    @classmethod
    def seed_from_environment(cls) -> tuple[bool, dict[str, Any], dict[str, Any]]:
        return (
            False,
            FAKE_SPEC.default_settings(),
            {"token": {"source": "environment", "env_var": "FAKE_CHANNEL_TOKEN"}},
        )

    async def start(self) -> None:
        if FakeChannel.fail_start:
            raise RuntimeError("boom")
        self.running = True

    async def stop(self) -> None:
        self.running = False
        self.stopped = True

    def status(self) -> AdapterStatus:
        if self.running:
            return AdapterStatus("connected", "Up", "@fake")
        return AdapterStatus("disconnected", "Down")

    async def send_test(self) -> None:
        if not self.running:
            raise ChannelError("not running")
        self.host.record_message(self.channel_id, "outbound")

    async def notify(self, content: str, event_type: str) -> bool:
        self.notified.append((event_type, content))
        return True


# tests/backend/conftest.py stubs ChannelManager.start so app lifespans never
# connect a real channel; these tests drive the real implementation.
_REAL_START = ChannelManager.start


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch):
    FakeChannel.instances = []
    FakeChannel.fail_start = False
    monkeypatch.setattr(ChannelManager, "start", _REAL_START)
    monkeypatch.delenv("FAKE_CHANNEL_TOKEN", raising=False)
    monkeypatch.delenv("PROGENA_PROVIDER_ENCRYPTION_KEY", raising=False)
    with patch.dict(CHANNEL_TYPES, {"fake": FakeChannel}, clear=True):
        yield


class Harness:
    def __init__(self) -> None:
        self.store = InMemoryDocumentStore()
        self.events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.ui_connected = False
        self.manager = ChannelManager(
            store_getter=lambda: self.store,
            runtime_client_getter=lambda: None,
            broadcast=self.broadcast,
            has_ui_subscribers=lambda: self.ui_connected,
        )

    def broadcast(self, kind: str, payload: dict[str, Any], **kwargs: Any) -> None:
        self.events.append((kind, payload, kwargs))

    def document(self, channel_id: str = "fake") -> dict[str, Any]:
        stored = self.store.get_document(PROGENA_STATE_PREFIX, f"channel:{channel_id}")
        assert stored is not None
        return stored.payload

    def write(self, channel_id: str = "fake", **changes: Any) -> None:
        """Write the document directly, as an agent or workflow would."""
        document = {**self.document(channel_id), **changes}
        self.store.save_document(PROGENA_STATE_PREFIX, f"channel:{channel_id}", document)
        self.manager.on_store_change(None, f"channel:{channel_id}")

    def last_snapshot(self) -> dict[str, Any]:
        snapshots = [payload for kind, payload, _ in self.events if kind == "channels.snapshot"]
        assert snapshots
        return snapshots[-1]

    def channel(self, channel_id: str = "fake") -> dict[str, Any]:
        return next(c for c in self.last_snapshot()["channels"] if c["id"] == channel_id)

    async def settle(self) -> None:
        for _ in range(200):
            pending = [task for task in self.manager._tasks if not task.done()]
            broadcast = self.manager._broadcast_task
            if broadcast is not None and not broadcast.done():
                pending.append(broadcast)
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)
        raise AssertionError("channel manager did not settle")


@pytest.fixture
def harness() -> Harness:
    return Harness()


# -- documents ---------------------------------------------------------------


def test_seed_creates_missing_documents_once(harness: Harness):
    assert seed_channels(harness.store) == ["fake"]
    document = harness.document()
    assert document["type"] == "fake"
    assert document["enabled"] is False
    assert document["settings"] == FAKE_SPEC.default_settings()

    harness.store.save_document(
        PROGENA_STATE_PREFIX, "channel:fake", {**document, "name": "Renamed"}
    )
    assert seed_channels(harness.store) == []
    assert harness.document()["name"] == "Renamed"


def test_telegram_seed_reads_legacy_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111, 222,,111")
    monkeypatch.setenv("TELEGRAM_POLLING_TIMEOUT", "900")  # out of range: default kept
    monkeypatch.setenv("TELEGRAM_RATE_LIMIT_PER_MINUTE", "12")

    enabled, settings, credentials = TelegramChannel.seed_from_environment()

    assert enabled is True
    assert settings["chat_id"] == "987654321"
    assert settings["allowed_chat_ids"] == ["111", "222"]
    assert settings["polling_timeout"] == 30
    assert settings["rate_limit_per_minute"] == 12
    assert credentials == {"bot_token": {"source": "environment", "env_var": "TELEGRAM_BOT_TOKEN"}}


def test_update_merges_partial_settings(harness: Harness):
    seed_channels(harness.store)

    update_channel(
        harness.store, "fake", {"settings": {"target": " room ", "chats": ["a", " a", "b", ""]}}
    )
    update_channel(harness.store, "fake", {"settings": {"retries": 5}, "enabled": True})

    document = harness.document()
    assert document["enabled"] is True
    assert document["settings"]["target"] == "room"
    assert document["settings"]["chats"] == ["a", "b"]
    assert document["settings"]["retries"] == 5


@pytest.mark.parametrize(
    ("patch_body", "message"),
    [
        ({}, "Body must be an object"),
        ({"name": "x"}, "Unsupported fields: name"),
        ({"enabled": "yes"}, "enabled must be a boolean"),
        ({"settings": {"nope": 1}}, "Unknown Fake settings: nope"),
        ({"settings": {"retries": 9}}, "settings.retries must be at most 5"),
        ({"settings": {"retries": 1.5}}, "settings.retries must be an integer"),
        ({"settings": {"target": ""}}, "settings.target is required"),
        ({"credentials": {"other": None}}, "credentials.other is not a Fake credential"),
        (
            {
                "credentials": {
                    "token": {"source": "environment", "env_var": "PROGENA_DATABASE_URL"}
                }
            },
            "credentials.token.env_var must be FAKE_CHANNEL_TOKEN",
        ),
        (
            {"credentials": {"token": {"source": "database", "value": "secret"}}},
            "PROGENA_PROVIDER_ENCRYPTION_KEY is required",
        ),
    ],
)
def test_update_rejects_invalid_patches(harness: Harness, patch_body, message):
    seed_channels(harness.store)
    before = harness.document()

    with pytest.raises(ValueError, match=message):
        update_channel(harness.store, "fake", patch_body)

    assert harness.document() == before


def test_update_unknown_channel(harness: Harness):
    with pytest.raises(LookupError):
        update_channel(harness.store, "missing", {"enabled": True})


def test_database_secret_is_encrypted_and_redacted(harness: Harness, monkeypatch):
    monkeypatch.setenv("PROGENA_PROVIDER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    seed_channels(harness.store)

    update_channel(
        harness.store,
        "fake",
        {"credentials": {"token": {"source": "database", "value": " s3cret "}}},
    )

    reference = harness.document()["credentials"]["token"]
    assert reference["source"] == "database"
    assert "s3cret" not in str(harness.document())
    assert resolve_secret(reference) == "s3cret"
    assert redacted_channel_document(harness.document())["credentials"] == {
        "token": {"source": "database", "env_var": None, "configured": True}
    }

    update_channel(harness.store, "fake", {"credentials": {"token": None}})
    assert harness.document()["credentials"] == {}


def test_invalid_stored_document_is_reported_not_raised(harness: Harness):
    harness.store.save_document(PROGENA_STATE_PREFIX, "channel:broken", {"id": "broken"})

    stored = load_channels(harness.store)

    assert stored["broken"].document is None
    assert "does not conform to schema" in (stored["broken"].error or "")


# -- manager -----------------------------------------------------------------


async def _started(harness: Harness, monkeypatch, **settings: Any) -> None:
    monkeypatch.setenv("FAKE_CHANNEL_TOKEN", "token-1")
    await harness.manager.start()
    await harness.settle()
    update_channel(
        harness.store,
        "fake",
        {"enabled": True, "settings": {"target": "room", **settings}},
    )
    harness.manager.on_store_change(None, "channel:fake")
    await harness.settle()


@pytest.mark.asyncio
async def test_disabled_and_unconfigured_channels_do_not_start(harness: Harness):
    await harness.manager.start()
    await harness.settle()
    assert harness.channel()["status"] == "disabled"

    update_channel(harness.store, "fake", {"enabled": True, "settings": {"target": "room"}})
    harness.manager.on_store_change(None, "channel:fake")
    await harness.settle()

    channel = harness.channel()
    assert channel["status"] == "unconfigured"
    assert channel["status_detail"] == "Token: environment variable FAKE_CHANNEL_TOKEN is not set"
    assert FakeChannel.instances == []
    await harness.manager.stop()


@pytest.mark.asyncio
async def test_enabled_channel_runs_and_restarts_only_on_change(harness: Harness, monkeypatch):
    await _started(harness, monkeypatch)
    [first] = FakeChannel.instances
    assert first.running
    assert first.secrets == {"token": "token-1"}
    channel = harness.channel()
    assert channel["status"] == "connected"
    assert channel["handle"] == "@fake"
    assert channel["stats"]["connected_at"] is not None

    # A rename is not a connection change.
    harness.write(name="Renamed")
    await harness.settle()
    assert FakeChannel.instances == [first]
    assert harness.channel()["name"] == "Renamed"

    harness.write(settings={**harness.document()["settings"], "retries": 4})
    await harness.settle()
    assert first.stopped
    assert len(FakeChannel.instances) == 2
    assert FakeChannel.instances[1].settings["retries"] == 4

    harness.write(enabled=False)
    await harness.settle()
    assert not FakeChannel.instances[1].running
    assert harness.channel()["status"] == "disabled"
    await harness.manager.stop()


@pytest.mark.asyncio
async def test_reconnect_restarts_and_unchanged_patch_clears_connecting(
    harness: Harness, monkeypatch
):
    await _started(harness, monkeypatch)

    harness.manager.request_reconnect("fake", force=True)
    assert harness.manager.status("fake", harness.document()).state == "connecting"
    await harness.settle()
    assert len(FakeChannel.instances) == 2
    assert harness.channel()["status"] == "connected"

    # A save that changes nothing must not leave the channel "connecting".
    harness.manager.request_reconnect("fake")
    await harness.settle()
    assert len(FakeChannel.instances) == 2
    assert harness.manager.status("fake", harness.document()).state == "connected"
    await harness.manager.stop()


@pytest.mark.asyncio
async def test_start_failure_is_an_error_status(harness: Harness, monkeypatch):
    FakeChannel.fail_start = True
    await _started(harness, monkeypatch)

    channel = harness.channel()
    assert channel["status"] == "error"
    assert channel["status_detail"] == "Failed to start: boom"
    assert FakeChannel.instances[0].stopped
    await harness.manager.stop()


@pytest.mark.asyncio
async def test_deleted_document_stops_its_adapter(harness: Harness, monkeypatch):
    await _started(harness, monkeypatch)
    [adapter] = FakeChannel.instances

    harness.store.delete_document(PROGENA_STATE_PREFIX, "channel:fake")
    harness.manager.on_store_change(None, "channel:fake")
    await harness.settle()

    assert adapter.stopped
    assert harness.last_snapshot()["channels"] == []
    await harness.manager.stop()


@pytest.mark.asyncio
async def test_notify_away_only_without_browser_and_when_enabled(harness: Harness, monkeypatch):
    await _started(harness, monkeypatch)
    [adapter] = FakeChannel.instances

    harness.ui_connected = True
    harness.manager.notify_away("agent_message", "hi")
    harness.ui_connected = False
    harness.manager.notify_away("status_update", "ignored")
    harness.manager.notify_away("agent_question", "")
    harness.manager.notify_away("agent_question", "are you there?")
    await harness.settle()
    assert adapter.notified == [("agent_question", "are you there?")]

    harness.write(settings={**harness.document()["settings"], "notify_when_away": False})
    await harness.settle()
    harness.manager.notify_away("agent_message", "quiet")
    await harness.settle()
    assert FakeChannel.instances[-1].notified == []
    await harness.manager.stop()


@pytest.mark.asyncio
async def test_snapshot_never_contains_secret_material(harness: Harness, monkeypatch):
    monkeypatch.setenv("PROGENA_PROVIDER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    await harness.manager.start()
    await harness.settle()
    update_channel(
        harness.store,
        "fake",
        {
            "enabled": True,
            "settings": {"target": "room"},
            "credentials": {"token": {"source": "database", "value": "s3cret"}},
        },
    )
    harness.manager.on_store_change(None, "channel:fake")
    await harness.settle()

    snapshot = harness.last_snapshot()
    ciphertext = harness.document()["credentials"]["token"]["ciphertext"]
    assert "s3cret" not in str(snapshot)
    assert ciphertext not in str(snapshot)
    assert snapshot["credential_storage"] == {"database": True}
    channel = harness.channel()
    assert channel["status"] == "connected"
    assert channel["credentials"] == [
        {
            "key": "token",
            "label": "Token",
            "help": "",
            "default_env_var": "FAKE_CHANNEL_TOKEN",
            "env_var_prefix": "PROGENA_CHANNEL_",
            "source": "database",
            "env_var": None,
            "configured": True,
        }
    ]
    assert [field["key"] for field in channel["fields"]] == [
        "target",
        "notify_when_away",
        "retries",
        "chats",
    ]
    await harness.manager.stop()


@pytest.mark.asyncio
async def test_messages_are_counted(harness: Harness, monkeypatch):
    await _started(harness, monkeypatch)

    harness.manager.record_message("fake", "inbound")
    await harness.manager.send_test("fake")
    await harness.settle()

    stats = harness.channel()["stats"]
    assert (stats["inbound"], stats["outbound"]) == (1, 1)
    assert stats["last_inbound_at"] and stats["last_outbound_at"]
    await harness.manager.stop()


# -- routes ------------------------------------------------------------------


def _client(harness: Harness) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def correlate_request(request, call_next):
        request.state.request_id = request.headers.get("X-Operation-Id", "channels-test")
        return await call_next(request)

    app.include_router(
        create_channels_router(lambda: harness.manager, lambda: harness.store, harness.broadcast),
        prefix="/api/ui",
    )
    return TestClient(app)


def test_get_publishes_correlated_snapshot(harness: Harness):
    seed_channels(harness.store)

    response = _client(harness).get("/api/ui/channels", headers={"X-Operation-Id": "op-1"})

    assert_acknowledged(response, ["channels.snapshot"], "op-1")
    [(kind, payload, kwargs)] = harness.events
    assert kind == "channels.snapshot"
    assert kwargs == {"request_id": "op-1"}
    assert [channel["id"] for channel in payload["channels"]] == ["fake"]
    assert payload["channels"][0]["status"] == "disabled"


def test_patch_persists_then_publishes(harness: Harness):
    seed_channels(harness.store)

    response = _client(harness).patch(
        "/api/ui/channels/fake",
        json={"settings": {"target": "room"}},
        headers={"X-Operation-Id": "op-2"},
    )

    assert_acknowledged(response, ["channels.snapshot"], "op-2")
    assert harness.document()["settings"]["target"] == "room"
    kind, payload, kwargs = harness.events[-1]
    assert (kind, kwargs) == ("channels.snapshot", {"request_id": "op-2"})
    assert payload["channels"][0]["settings"]["target"] == "room"


@pytest.mark.parametrize(
    ("path", "body", "status"),
    [
        ("/api/ui/channels/fake", {"settings": {"retries": 0}}, 422),
        ("/api/ui/channels/fake", [], 422),
        ("/api/ui/channels/missing", {"enabled": True}, 404),
    ],
)
def test_patch_failures_publish_nothing(harness: Harness, path, body, status):
    seed_channels(harness.store)

    response = _client(harness).patch(path, json=body)

    assert response.status_code == status
    assert harness.events == []


def test_test_route_reports_failure_as_event(harness: Harness):
    seed_channels(harness.store)

    response = _client(harness).post(
        "/api/ui/channels/fake/test", headers={"X-Operation-Id": "op-3"}
    )

    assert_acknowledged(response, ["channel.tested"], "op-3")
    assert harness.events == [
        (
            "channel.tested",
            {"channel_id": "fake", "ok": False, "error": "Channel is turned off"},
            {"request_id": "op-3"},
        )
    ]


def test_reconnect_requires_enabled_channel(harness: Harness):
    seed_channels(harness.store)
    client = _client(harness)

    assert client.post("/api/ui/channels/fake/reconnect").status_code == 409
    assert client.post("/api/ui/channels/missing/reconnect").status_code == 404

    update_channel(harness.store, "fake", {"enabled": True})
    response = client.post("/api/ui/channels/fake/reconnect", headers={"X-Operation-Id": "op-4"})

    assert_acknowledged(response, ["channels.snapshot"], "op-4")
    kind, payload, kwargs = harness.events[-1]
    assert (kind, kwargs) == ("channels.snapshot", {"request_id": "op-4"})
    assert payload["channels"][0]["enabled"] is True
