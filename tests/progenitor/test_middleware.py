"""
Tests for Phase 4: Custom middleware for DeepAgent.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.agent.middleware import (
    AuditLoggingMiddleware,
    MetricsMiddleware,
    SecretRedactionMiddleware,
)


class TestSecretRedactionMiddleware:
    """Tests for the secret redaction middleware."""

    def test_instantiation(self):
        mw = SecretRedactionMiddleware()
        assert mw is not None

    def test_sync_wrap_passes_through(self):
        """Sync wrap_tool_call just passes through."""
        mw = SecretRedactionMiddleware()
        handler = MagicMock(return_value="result")
        request = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once_with(request)
        assert result == "result"

    async def test_async_wrap_passes_through(self):
        """Async awrap_tool_call passes through to handler."""
        mw = SecretRedactionMiddleware()
        handler = AsyncMock(return_value="result")
        request = MagicMock()
        request.tool_call = {
            "name": "shell",
            "args": {"command": "echo hello"},
            "id": "tc_1",
        }
        # awrap_tool_call returns the coroutine from handler
        await mw.awrap_tool_call(request, handler)
        # It should call handler with the original request (not redacted)
        handler.assert_called_once_with(request)


class TestAuditLoggingMiddleware:
    """Tests for the audit logging middleware."""

    def test_instantiation_no_dir(self):
        mw = AuditLoggingMiddleware()
        assert mw._log_dir is None

    def test_instantiation_with_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = AuditLoggingMiddleware(log_dir=tmpdir)
            assert mw._log_dir == Path(tmpdir)

    def test_creates_log_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "nested" / "logs"
            AuditLoggingMiddleware(log_dir=log_dir)
            assert log_dir.exists()

    async def test_logs_successful_call(self):
        """Logs a successful tool call with timing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = AuditLoggingMiddleware(log_dir=tmpdir)
            handler = AsyncMock(return_value="tool_result")
            request = MagicMock()
            request.tool_call = {
                "name": "current_time",
                "args": {},
                "id": "tc_1",
            }

            result = await mw.awrap_tool_call(request, handler)
            assert result == "tool_result"

            # Check JSONL log
            log_file = Path(tmpdir) / "tool_calls.jsonl"
            assert log_file.exists()
            with open(log_file) as f:
                entry = json.loads(f.readline())
            assert entry["tool_name"] == "current_time"
            assert entry["success"] is True
            assert entry["error"] is None
            assert "duration_ms" in entry
            assert "timestamp" in entry

    async def test_logs_failed_call(self):
        """Logs a failed tool call with error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = AuditLoggingMiddleware(log_dir=tmpdir)
            handler = AsyncMock(side_effect=RuntimeError("command failed"))
            request = MagicMock()
            request.tool_call = {
                "name": "shell",
                "args": {"command": "false"},
                "id": "tc_2",
            }

            with pytest.raises(RuntimeError, match="command failed"):
                await mw.awrap_tool_call(request, handler)

            log_file = Path(tmpdir) / "tool_calls.jsonl"
            with open(log_file) as f:
                entry = json.loads(f.readline())
            assert entry["tool_name"] == "shell"
            assert entry["success"] is False
            assert "command failed" in entry["error"]

    async def test_redacts_secrets_in_log(self):
        """Secrets in args are redacted in the log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = AuditLoggingMiddleware(log_dir=tmpdir)
            handler = AsyncMock(return_value="ok")
            request = MagicMock()
            request.tool_call = {
                "name": "shell",
                "args": {"command": "export OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz12345"},
                "id": "tc_3",
            }

            await mw.awrap_tool_call(request, handler)

            log_file = Path(tmpdir) / "tool_calls.jsonl"
            with open(log_file) as f:
                entry = json.loads(f.readline())
            # The secret should be redacted in the log
            assert "sk-proj-" not in entry["args"]["command"]
            assert "REDACTED" in entry["args"]["command"]

    def test_sync_wrap_passes_through(self):
        mw = AuditLoggingMiddleware()
        handler = MagicMock(return_value="result")
        request = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once_with(request)
        assert result == "result"


class TestMetricsMiddleware:
    """Tests for the metrics middleware."""

    def test_initial_metrics(self):
        mw = MetricsMiddleware()
        metrics = mw.get_metrics()
        assert metrics["model_calls"] == 0
        assert metrics["tool_calls"] == 0
        assert metrics["tool_errors"] == 0
        assert metrics["tools"] == {}

    async def test_tracks_tool_call(self):
        mw = MetricsMiddleware()
        handler = AsyncMock(return_value="result")
        request = MagicMock()
        request.tool_call = {"name": "shell", "args": {}, "id": "tc_1"}

        await mw.awrap_tool_call(request, handler)

        metrics = mw.get_metrics()
        assert metrics["tool_calls"] == 1
        assert "shell" in metrics["tools"]
        assert metrics["tools"]["shell"]["calls"] == 1
        assert metrics["tools"]["shell"]["errors"] == 0

    async def test_tracks_multiple_tools(self):
        mw = MetricsMiddleware()
        handler = AsyncMock(return_value="ok")

        for name in ["shell", "git", "shell", "shell", "git"]:
            request = MagicMock()
            request.tool_call = {"name": name, "args": {}, "id": "tc"}
            await mw.awrap_tool_call(request, handler)

        metrics = mw.get_metrics()
        assert metrics["tool_calls"] == 5
        assert metrics["tools"]["shell"]["calls"] == 3
        assert metrics["tools"]["git"]["calls"] == 2

    async def test_tracks_errors(self):
        mw = MetricsMiddleware()
        handler = AsyncMock(side_effect=RuntimeError("boom"))
        request = MagicMock()
        request.tool_call = {"name": "shell", "args": {}, "id": "tc_1"}

        with pytest.raises(RuntimeError):
            await mw.awrap_tool_call(request, handler)

        metrics = mw.get_metrics()
        assert metrics["tool_errors"] == 1
        assert metrics["tools"]["shell"]["errors"] == 1
        assert metrics["tools"]["shell"]["calls"] == 1

    def test_tracks_model_calls(self):
        mw = MetricsMiddleware()
        mw.after_model(MagicMock(), MagicMock())
        mw.after_model(MagicMock(), MagicMock())
        assert mw.get_metrics()["model_calls"] == 2

    async def test_tracks_latency(self):
        mw = MetricsMiddleware()

        async def slow_handler(req):
            import asyncio
            await asyncio.sleep(0.05)
            return "done"

        request = MagicMock()
        request.tool_call = {"name": "slow_tool", "args": {}, "id": "tc"}
        await mw.awrap_tool_call(request, slow_handler)

        metrics = mw.get_metrics()
        assert metrics["tools"]["slow_tool"]["total_ms"] >= 40  # At least 40ms
        assert metrics["tools"]["slow_tool"]["avg_ms"] >= 40

    def test_reset(self):
        mw = MetricsMiddleware()
        mw._call_counts["shell"] = 5
        mw._model_calls = 3
        mw.reset()
        metrics = mw.get_metrics()
        assert metrics["tool_calls"] == 0
        assert metrics["model_calls"] == 0


class TestMiddlewareIntegration:
    """Integration test: middleware wired into factory."""

    def test_factory_accepts_middleware_param(self):
        """create_progenitor_agent accepts log_dir param."""
        from unittest.mock import patch

        with patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create:
            mock_create.return_value = MagicMock()
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.deep_agent import create_progenitor_agent

                with tempfile.TemporaryDirectory() as tmpdir:
                    create_progenitor_agent(log_dir=tmpdir)

                # Verify middleware was passed
                call_kwargs = mock_create.call_args.kwargs
                assert "middleware" in call_kwargs
                middleware = call_kwargs["middleware"]
                types = [type(m).__name__ for m in middleware]
                assert "SecretRedactionMiddleware" in types
                assert "AuditLoggingMiddleware" in types
                assert "MetricsMiddleware" in types
