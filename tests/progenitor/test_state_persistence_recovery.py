"""Recovery tests for low-level state persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.progenitor.state.persistence import (
    PERSISTED_STATE_SCHEMA_VERSION,
    atomic_write_json,
    read_json_file,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("current_state.json", '{"messages": '),
        ("schedule.json", "["),
    ],
)
async def test_read_json_file_quarantines_malformed_payloads(
    tmp_path: Path,
    filename: str,
    payload: str,
):
    """Malformed JSON should be rotated out of the active state path."""
    filepath = tmp_path / filename
    filepath.write_text(payload, encoding="utf-8")

    result = await read_json_file(filepath)

    assert result.data is None
    assert result.broken_path is not None
    assert filepath.exists() is False
    assert result.broken_path.read_text(encoding="utf-8") == payload


@pytest.mark.asyncio
async def test_atomic_write_json_cleans_up_temp_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Rename failures should raise cleanly and leave no orphaned temp files behind."""
    filepath = tmp_path / "current_state.json"
    original = {"schema_version": PERSISTED_STATE_SCHEMA_VERSION, "messages": ["stable"]}
    filepath.write_text(json.dumps(original), encoding="utf-8")

    def fail_replace(_src: str, _dst: str) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr("app.progenitor.state.persistence.os.replace", fail_replace)

    with pytest.raises(OSError, match="No space left on device"):
        await atomic_write_json(filepath, {"messages": ["new"]})

    assert json.loads(filepath.read_text(encoding="utf-8")) == original
    assert list(tmp_path.glob(".current_state.json.*.tmp")) == []


@pytest.mark.asyncio
async def test_read_json_file_preserves_future_schema_versions(tmp_path: Path):
    """Future schema versions should be left untouched instead of rewritten."""
    filepath = tmp_path / "current_state.json"
    payload = {
        "schema_version": PERSISTED_STATE_SCHEMA_VERSION + 1,
        "messages": [],
    }
    filepath.write_text(json.dumps(payload), encoding="utf-8")

    result = await read_json_file(filepath)

    assert result.data == payload
    assert result.needs_persist is False
    assert filepath.exists() is True
