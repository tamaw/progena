"""Tests for backend process logging configuration."""

from __future__ import annotations

import logging

from src.backend.logging_setup import setup_logging


def test_setup_logging_splits_records_and_drops_duplicate_access_lines(tmp_path, monkeypatch):
    """Backend and uvicorn records reach the files; access lines are not duplicated."""
    original_handlers = logging.root.handlers[:]
    original_level = logging.root.level
    uvicorn_access = logging.getLogger("uvicorn.access")
    original_access_handlers = uvicorn_access.handlers[:]
    original_access_propagate = uvicorn_access.propagate
    original_access_level = uvicorn_access.level
    monkeypatch.setenv("PROGENA_LOG_DIR", str(tmp_path))

    try:
        setup_logging()
        logging.getLogger("src.backend.test").info("backend info")
        uvicorn_access.info("GET /healthz 200")
        logging.getLogger("uvicorn.error").info("Application startup complete.")
        logging.getLogger("src.backend.test").error("backend error")

        for handler in logging.root.handlers:
            handler.flush()

        stdout_log = (tmp_path / "backend" / "backend.stdout.log").read_text(encoding="utf-8")
        stderr_log = (tmp_path / "backend" / "backend.stderr.log").read_text(encoding="utf-8")

        assert "backend info" in stdout_log
        assert "Application startup complete." in stdout_log
        assert "GET /healthz 200" not in stdout_log
        assert "backend error" not in stdout_log
        assert stderr_log.count("backend error") == 1
    finally:
        for handler in logging.root.handlers[:]:
            handler.close()
            logging.root.removeHandler(handler)
        logging.root.handlers = original_handlers
        logging.root.setLevel(original_level)
        uvicorn_access.handlers = original_access_handlers
        uvicorn_access.propagate = original_access_propagate
        uvicorn_access.setLevel(original_access_level)
