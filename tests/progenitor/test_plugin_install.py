"""Tests for plugin reconciler and update checker."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.plugins.install import check_plugin_updates, reconcile_plugins
from app.progenitor.plugins.sources import PluginFetchError
from tests.progenitor.test_backends import FakeContentStateManager


class FakePluginStateManager(FakeContentStateManager):
    """Extended fake state manager with plugins config methods."""

    def __init__(self) -> None:
        super().__init__()
        self._plugins: dict[str, Any] = {"plugins": {}}
        self.save_plugins_config_calls = 0
        self.save_content_document_calls = 0
        self.delete_content_document_calls = 0

    async def get_plugins_config(self) -> dict[str, Any]:
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in self._plugins.items()}

    async def save_plugins_config(self, payload: dict[str, Any]) -> None:
        self.save_plugins_config_calls += 1
        self._plugins = payload

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        self.save_content_document_calls += 1
        await super().save_content_document(kind, relative_path, content)

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        self.delete_content_document_calls += 1
        await super().delete_content_document(kind, relative_path)


@pytest.fixture
def state_manager():
    return FakePluginStateManager()


@pytest.fixture
def fake_files():
    return {"quality-review/SKILL.md": "---\nname: quality-review\n---\n# Quality Review\n"}


@pytest.fixture
def fake_digest():
    return "sha256:abc123"


@pytest.mark.asyncio
async def test_installs_new_entry(state_manager, fake_files, fake_digest):
    """A new plugin entry is fetched and its skill documents are written."""
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "version": "2.1.0",
            }
        }
    }

    with (
        patch(
            "app.progenitor.plugins.install.fetch_plugin_files_with_report",
            new=AsyncMock(return_value=(fake_files, 0)),
        ) as mock_fetch,
        patch("app.progenitor.plugins.install.compute_digest", return_value=fake_digest),
    ):
        summary = await reconcile_plugins(state_manager)

    mock_fetch.assert_awaited_once()
    assert summary["installed"] == ["acme-tools/formatter"]
    assert summary["removed"] == []
    assert summary["errored"] == []

    # Skill document written under correct segment
    assert (
        state_manager._docs["skill"]["acme-tools__formatter/quality-review/SKILL.md"]
        == fake_files["quality-review/SKILL.md"]
    )

    # Runtime fields updated
    config = state_manager._plugins["plugins"]["acme-tools/formatter"]
    assert config["status"] == "installed"
    assert config["installed_version"] == "2.1.0"
    assert config["installed_digest"] == fake_digest
    assert config["skills"] == ["quality-review"]
    assert config["available_version"] is None


@pytest.mark.asyncio
async def test_removes_plugin_when_entry_dropped(state_manager, fake_files, fake_digest):
    """Deleting a plugin entry removes its skill docs but leaves built-ins."""
    # Seed a previously-installed plugin
    state_manager._docs["skill"]["acme-tools__formatter/quality-review/SKILL.md"] = fake_files[
        "quality-review/SKILL.md"
    ]
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "version": "2.1.0",
                "installed_version": "2.1.0",
                "installed_digest": fake_digest,
                "skills": ["quality-review"],
                "status": "installed",
            }
        }
    }

    # Now remove the entry
    state_manager._plugins = {"plugins": {}}

    summary = await reconcile_plugins(state_manager)

    assert summary["removed"] == ["acme-tools__formatter"]
    assert "acme-tools__formatter/quality-review/SKILL.md" not in state_manager._docs["skill"]
    assert state_manager.delete_content_document_calls == 1


@pytest.mark.asyncio
async def test_removes_stale_unsegmented_skill_rows(state_manager, fake_files, fake_digest):
    """Native skills are disk-only now (never seeded into Postgres), so any
    leftover unsegmented ("no __") row is stale and gets cleaned up too."""
    state_manager._docs["skill"]["progenitor/managing-mcp/SKILL.md"] = (
        "---\nname: managing-mcp\n---\n"
    )
    state_manager._docs["skill"]["builder_agent/build/SKILL.md"] = "---\nname: build\n---\n"
    state_manager._plugins = {"plugins": {}}

    summary = await reconcile_plugins(state_manager)

    assert set(summary["removed"]) == {"progenitor", "builder_agent"}
    assert "progenitor/managing-mcp/SKILL.md" not in state_manager._docs["skill"]
    assert "builder_agent/build/SKILL.md" not in state_manager._docs["skill"]


@pytest.mark.asyncio
async def test_fetch_error_sets_error_status(state_manager, fake_files, fake_digest):
    """One failing plugin must not abort reconciling another entry."""
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "version": "2.1.0",
            },
            "bad-marketplace/bad-plugin": {
                "source": {"source": "npm", "package": "x"},
                "version": "1.0.0",
            },
        }
    }

    async def side_effect(catalog, plugin_name):
        if plugin_name == "bad-plugin":
            raise PluginFetchError("npm sources are not supported")
        return (fake_files, 0)

    with (
        patch(
            "app.progenitor.plugins.install.fetch_plugin_files_with_report",
            new=AsyncMock(side_effect=side_effect),
        ),
        patch("app.progenitor.plugins.install.compute_digest", return_value=fake_digest),
    ):
        summary = await reconcile_plugins(state_manager)

    assert summary["installed"] == ["acme-tools/formatter"]
    assert summary["errored"] == ["bad-marketplace/bad-plugin"]

    good = state_manager._plugins["plugins"]["acme-tools/formatter"]
    assert good["status"] == "installed"

    bad = state_manager._plugins["plugins"]["bad-marketplace/bad-plugin"]
    assert bad["status"] == "error"


@pytest.mark.asyncio
async def test_malformed_key_does_not_abort_other_entries(state_manager, fake_files, fake_digest):
    """A key without a '/' must fail only itself, never abort the pass.

    Found live via a manually-corrupted store row (bypassing
    validate_plugins_document, which normally rejects this key shape on
    every write path): `key.split("/", 1)` used to run *before* the
    per-entry try/except, so an unpack ValueError on one malformed key
    crashed the whole reconcile pass -- every other, valid entry silently
    never got reconciled that round. The store-write path always validates
    keys before persisting, so this should be unreachable in practice, but
    the reconciler must stay defensive against it regardless.
    """
    state_manager._plugins = {
        "plugins": {
            "malformed-key-no-slash": {"source": "x"},
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "version": "2.1.0",
            },
        }
    }

    with (
        patch(
            "app.progenitor.plugins.install.fetch_plugin_files_with_report",
            new=AsyncMock(return_value=(fake_files, 0)),
        ),
        patch("app.progenitor.plugins.install.compute_digest", return_value=fake_digest),
    ):
        summary = await reconcile_plugins(state_manager)

    assert summary["installed"] == ["acme-tools/formatter"]
    assert summary["errored"] == ["malformed-key-no-slash"]

    good = state_manager._plugins["plugins"]["acme-tools/formatter"]
    assert good["status"] == "installed"
    bad = state_manager._plugins["plugins"]["malformed-key-no-slash"]
    assert bad["status"] == "error"


@pytest.mark.asyncio
async def test_persistent_error_does_not_loop_forever(state_manager, fake_digest):
    """A persistently-failing entry must stop writing after its first error.

    Found live: the event-driven store listener re-triggers reconcile_plugins
    on every save. If the error branch bumped last_checked unconditionally
    (a fresh timestamp differs every pass), that alone made the pass look
    "changed", so it saved again, re-triggering itself -- an infinite loop
    that hammered Postgres with writes until the process was killed.
    """
    state_manager._plugins = {
        "plugins": {
            "bad-marketplace/bad-plugin": {
                "source": {"source": "npm", "package": "x"},
                "version": "1.0.0",
            },
        }
    }

    with patch(
        "app.progenitor.plugins.install.fetch_plugin_files_with_report",
        new=AsyncMock(side_effect=PluginFetchError("npm sources are not supported")),
    ):
        first = await reconcile_plugins(state_manager)
        assert first["errored"] == ["bad-marketplace/bad-plugin"]
        assert state_manager.save_plugins_config_calls == 1

        # Second and third passes against the same still-failing entry must
        # make ZERO further writes -- this is what stops the loop.
        state_manager.save_plugins_config_calls = 0
        second = await reconcile_plugins(state_manager)
        third = await reconcile_plugins(state_manager)

    assert second == {"installed": [], "removed": [], "updated_available": [], "errored": []}
    assert third == {"installed": [], "removed": [], "updated_available": [], "errored": []}
    assert state_manager.save_plugins_config_calls == 0


@pytest.mark.asyncio
async def test_check_plugin_updates_persistent_error_does_not_loop_forever(state_manager):
    """Same infinite-loop guard, for check_plugin_updates's error branch."""
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "installed_version": "2.1.0",
                "installed_digest": "sha256:abc123",
                "status": "installed",
            }
        }
    }

    with patch(
        "app.progenitor.plugins.install.fetch_plugin_files_with_report",
        new=AsyncMock(side_effect=PluginFetchError("network unreachable")),
    ):
        first = await check_plugin_updates(state_manager)
        assert first["errored"] == ["acme-tools/formatter"]
        assert state_manager.save_plugins_config_calls == 1

        state_manager.save_plugins_config_calls = 0
        second = await check_plugin_updates(state_manager)
        third = await check_plugin_updates(state_manager)

    assert second == {"updated_available": [], "errored": ["acme-tools/formatter"]}
    assert third == {"updated_available": [], "errored": ["acme-tools/formatter"]}
    assert state_manager.save_plugins_config_calls == 0


@pytest.mark.asyncio
async def test_idempotent_second_pass(state_manager, fake_files, fake_digest):
    """A second reconcile with no changes makes zero writes."""
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "version": "2.1.0",
            }
        }
    }

    with (
        patch(
            "app.progenitor.plugins.install.fetch_plugin_files_with_report",
            new=AsyncMock(return_value=(fake_files, 0)),
        ),
        patch("app.progenitor.plugins.install.compute_digest", return_value=fake_digest),
    ):
        await reconcile_plugins(state_manager)

        # Second pass with identical state
        state_manager.save_plugins_config_calls = 0
        state_manager.save_content_document_calls = 0
        summary = await reconcile_plugins(state_manager)

    assert summary == {"installed": [], "removed": [], "updated_available": [], "errored": []}
    assert state_manager.save_plugins_config_calls == 0
    assert state_manager.save_content_document_calls == 0


@pytest.mark.asyncio
async def test_frozen_entry_with_unchanged_digest(state_manager, fake_files, fake_digest):
    """A frozen entry (version unset, installed_version set) with unchanged upstream never touches installed_version."""
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "installed_version": "2.1.0",
                "installed_digest": fake_digest,
                "status": "installed",
            }
        }
    }

    with (
        patch(
            "app.progenitor.plugins.install.fetch_plugin_files_with_report",
            new=AsyncMock(return_value=(fake_files, 0)),
        ),
        patch("app.progenitor.plugins.install.compute_digest", return_value=fake_digest),
    ):
        summary = await reconcile_plugins(state_manager)

    assert summary["installed"] == []
    assert summary["removed"] == []
    assert summary["updated_available"] == []
    assert summary["errored"] == []

    config = state_manager._plugins["plugins"]["acme-tools/formatter"]
    assert config["installed_version"] == "2.1.0"
    assert config["installed_digest"] == fake_digest
    assert config.get("available_version") is None


@pytest.mark.asyncio
async def test_frozen_entry_with_changed_digest_sets_available_version(
    state_manager, fake_files, fake_digest
):
    """Digest drift on a frozen entry sets available_version without changing installed_version."""
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "installed_version": "2.1.0",
                "installed_digest": fake_digest,
                "status": "installed",
            }
        }
    }

    new_digest = "sha256:def456"
    with (
        patch(
            "app.progenitor.plugins.install.fetch_plugin_files_with_report",
            new=AsyncMock(return_value=(fake_files, 0)),
        ),
        patch("app.progenitor.plugins.install.compute_digest", return_value=new_digest),
    ):
        summary = await reconcile_plugins(state_manager)

    assert summary["updated_available"] == ["acme-tools/formatter"]

    config = state_manager._plugins["plugins"]["acme-tools/formatter"]
    assert config["installed_version"] == "2.1.0"
    assert config["installed_digest"] == fake_digest
    assert config["available_version"] == "v2.1.0"


@pytest.mark.asyncio
async def test_check_plugin_updates_sets_available_version(state_manager, fake_files, fake_digest):
    """``check_plugin_updates`` detects upstream drift and sets available_version."""
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "installed_version": "2.1.0",
                "installed_digest": fake_digest,
                "status": "installed",
            }
        }
    }

    new_digest = "sha256:def456"
    with (
        patch(
            "app.progenitor.plugins.install.fetch_plugin_files_with_report",
            new=AsyncMock(return_value=(fake_files, 0)),
        ),
        patch("app.progenitor.plugins.install.compute_digest", return_value=new_digest),
    ):
        summary = await check_plugin_updates(state_manager)

    assert summary["updated_available"] == ["acme-tools/formatter"]
    assert summary["errored"] == []

    config = state_manager._plugins["plugins"]["acme-tools/formatter"]
    assert config["available_version"] == "v2.1.0"
    assert config["installed_version"] == "2.1.0"
    # No skill documents should have been written/deleted
    assert state_manager.save_content_document_calls == 0


@pytest.mark.asyncio
async def test_check_plugin_updates_skips_not_installed(state_manager):
    """``check_plugin_updates`` ignores entries that have never been installed."""
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "version": "2.1.0",
            }
        }
    }

    summary = await check_plugin_updates(state_manager)

    assert summary == {"updated_available": [], "errored": []}
    assert state_manager.save_plugins_config_calls == 0


@pytest.mark.asyncio
async def test_reconcile_plugins_emits_audit_log(state_manager, fake_files, fake_digest):
    """An actual change writes one audit record per affected entry."""
    state_manager._plugins = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "version": "2.1.0",
            }
        }
    }

    log_sink = MagicMock()
    log_sink.write_log = AsyncMock()

    with (
        patch(
            "app.progenitor.plugins.install.fetch_plugin_files_with_report",
            new=AsyncMock(return_value=(fake_files, 0)),
        ),
        patch("app.progenitor.plugins.install.compute_digest", return_value=fake_digest),
    ):
        await reconcile_plugins(state_manager, log_sink=log_sink)

    log_sink.write_log.assert_awaited_once()
    call_kwargs = log_sink.write_log.await_args.kwargs
    assert call_kwargs["source"] == "plugins"
    assert "acme-tools/formatter" in call_kwargs["message"]
