"""Tests for graceful process termination in CopilotCLI."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGracefulTermination(unittest.IsolatedAsyncioTestCase):
    """Test graceful process termination behavior."""

    async def asyncSetUp(self):
        """Set up test fixtures."""
        # Create a temporary config file
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "copilot.json"
        self.config_path.write_text(json.dumps({
            "command": "echo",
            "default_flags": [],
            "timeout": 1,  # Short timeout for tests
            "model": "test-model"
        }))

    async def test_terminate_process_gracefully_sigterm_succeeds(self):
        """Test that graceful termination works when process responds to SIGTERM."""
        from app.progenitor.tools.copilot import CopilotCLI

        cli = CopilotCLI(config_path=self.config_path)

        # Create a mock process that terminates on SIGTERM
        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        # Process terminates immediately after SIGTERM
        async def mock_wait():
            return 0
        mock_process.wait = mock_wait

        await cli._terminate_process_gracefully(mock_process, grace_period=1.0)

        # SIGTERM should be called
        mock_process.terminate.assert_called_once()
        # SIGKILL should NOT be called since SIGTERM succeeded
        mock_process.kill.assert_not_called()

    async def test_terminate_process_gracefully_needs_sigkill(self):
        """Test that SIGKILL is sent when SIGTERM times out."""
        from app.progenitor.tools.copilot import CopilotCLI

        cli = CopilotCLI(config_path=self.config_path)

        # Create a mock process that ignores SIGTERM
        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        # First wait (for SIGTERM) times out, second wait (for SIGKILL) succeeds
        call_count = 0
        async def mock_wait():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call - simulate SIGTERM being ignored by hanging forever
                await asyncio.sleep(10)  # Will be cancelled by timeout
            return 0

        mock_process.wait = mock_wait

        await cli._terminate_process_gracefully(mock_process, grace_period=0.1)

        # Both SIGTERM and SIGKILL should be called
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()

    async def test_terminate_process_already_terminated(self):
        """Test handling of already terminated process."""
        from app.progenitor.tools.copilot import CopilotCLI

        cli = CopilotCLI(config_path=self.config_path)

        # Create a mock process that raises ProcessLookupError
        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.terminate = MagicMock(side_effect=ProcessLookupError())

        # Should not raise an exception
        await cli._terminate_process_gracefully(mock_process)
        mock_process.terminate.assert_called_once()

    async def test_run_command_timeout_uses_graceful_termination(self):
        """Test that _run_command uses graceful termination on timeout."""
        from app.progenitor.tools.copilot import CopilotCLI

        cli = CopilotCLI(config_path=self.config_path)
        cli.config["timeout"] = 0.1  # Very short timeout

        # Track if graceful termination was called
        graceful_term_called = False
        original_terminate = cli._terminate_process_gracefully

        async def mock_terminate(process, grace_period=5.0):
            nonlocal graceful_term_called
            graceful_term_called = True
            process.kill()
            await process.wait()

        cli._terminate_process_gracefully = mock_terminate

        # Run a command that will timeout (sleep is cross-platform)
        # Use Python itself to sleep, as it's guaranteed to exist
        result = await cli._run_command(
            ["python", "-c", "import time; time.sleep(10)"],
            cwd=Path(self.temp_dir)
        )

        assert result["returncode"] == -1
        assert "timed out" in result["stderr"]
        assert graceful_term_called, "Graceful termination should have been called"

    async def test_terminate_with_exception(self):
        """Test handling of unexpected exceptions during termination."""
        from app.progenitor.tools.copilot import CopilotCLI

        cli = CopilotCLI(config_path=self.config_path)

        # Create a mock process that raises an unexpected error
        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.terminate = MagicMock(side_effect=RuntimeError("Unexpected error"))

        # Should not raise an exception
        await cli._terminate_process_gracefully(mock_process)
        mock_process.terminate.assert_called_once()


class TestCopilotCLIConfig(unittest.IsolatedAsyncioTestCase):
    """Test CopilotCLI configuration handling."""

    async def test_default_timeout_from_constructor(self):
        """Test that timeout can be set from constructor."""
        from app.progenitor.tools.copilot import CopilotCLI

        # Create a temp dir with no config file
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / "nonexistent.json"

        cli = CopilotCLI(config_path=config_path, timeout=600)
        assert cli.config["timeout"] == 600

    async def test_config_file_timeout_override(self):
        """Test that config file timeout takes precedence."""
        from app.progenitor.tools.copilot import CopilotCLI

        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / "copilot.json"
        config_path.write_text(json.dumps({
            "command": "copilot",
            "timeout": 900
        }))

        cli = CopilotCLI(config_path=config_path, timeout=300)
        # Config file value should be used
        assert cli.config["timeout"] == 900

    async def test_config_file_uses_instance_timeout_if_not_specified(self):
        """Test that instance timeout is used when config file doesn't specify."""
        from app.progenitor.tools.copilot import CopilotCLI

        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / "copilot.json"
        config_path.write_text(json.dumps({
            "command": "copilot",
            # No timeout specified
        }))

        cli = CopilotCLI(config_path=config_path, timeout=450)
        # Instance timeout should be used
        assert cli.config["timeout"] == 450


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
