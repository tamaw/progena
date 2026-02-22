"""Shared fixtures for progenitor unit tests."""


import pytest


@pytest.fixture(autouse=True)
def patch_checkpointer(monkeypatch):
    """Replace _build_checkpointer with an in-memory saver for all unit tests.

    Unit tests do not need SQLite persistence and must not attempt to create
    filesystem paths like /data/state that are inaccessible in CI.
    """
    from langgraph.checkpoint.memory import MemorySaver

    monkeypatch.setattr(
        "app.progenitor.agent.deep_agent._build_checkpointer",
        lambda db_path: MemorySaver(),
    )
