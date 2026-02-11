"""Tests for Copilot ACP (Agent Client Protocol) integration."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCopilotACPManager(unittest.IsolatedAsyncioTestCase):
    """Test CopilotACPManager connection and communication."""

    async def asyncSetUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())

    async def test_build_args_basic(self):
        """Test that basic args are built correctly."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager

        manager = CopilotACPManager(copilot_cmd="copilot")
        args = manager._build_args()

        assert "--acp" in args
        assert "--yolo" in args  # Default yolo=True

    async def test_build_args_with_model(self):
        """Test args with model specified."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager

        manager = CopilotACPManager(copilot_cmd="copilot", model="claude-sonnet-4")
        args = manager._build_args()

        assert "--model" in args
        model_idx = args.index("--model")
        assert args[model_idx + 1] == "claude-sonnet-4"

    async def test_build_args_with_allowed_dirs(self):
        """Test args with restricted directories."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager

        manager = CopilotACPManager(
            copilot_cmd="copilot",
            allowed_dirs=["/path/to/dir1", "/path/to/dir2"]
        )
        args = manager._build_args()

        assert args.count("--add-dir") == 2
        assert "/path/to/dir1" in args
        assert "/path/to/dir2" in args

    async def test_build_args_no_yolo(self):
        """Test args without yolo flag."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager

        manager = CopilotACPManager(copilot_cmd="copilot", yolo=False)
        args = manager._build_args()

        assert "--yolo" not in args
        assert "--acp" in args

    async def test_manager_initialization_logging(self):
        """Test that manager logs initialization."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager

        with patch("app.progenitor.runtime.copilot_acp.logger") as mock_logger:
            CopilotACPManager(
                copilot_cmd="test-copilot",
                model="test-model",
                yolo=True,
                allowed_dirs=["/test/dir"],
            )

            mock_logger.info.assert_called()
            call_args = str(mock_logger.info.call_args)
            assert "test-copilot" in call_args or "COPILOT_ACP" in call_args


class TestProgenitorACPClient(unittest.IsolatedAsyncioTestCase):
    """Test ProgenitorACPClient callback handling."""

    async def test_client_initialization(self):
        """Test client initializes correctly."""
        from app.progenitor.runtime.copilot_acp import ProgenitorACPClient

        client = ProgenitorACPClient(work_item_id="test_123")

        assert client.work_item_id == "test_123"
        assert len(client.chunks) == 0
        assert len(client.text_parts) == 0
        assert len(client.tool_calls) == 0
        assert not client.has_error()

    async def test_client_collects_text_output(self):
        """Test that client collects text from AgentMessageChunk."""
        from app.progenitor.runtime.copilot_acp import ProgenitorACPClient

        client = ProgenitorACPClient(work_item_id="test")

        # Simulate AgentMessageChunk
        class MockAgentMessageChunk:
            def __init__(self, text):
                self.text = text

        # Can't easily test session_update without full SDK setup
        # but we can test the output collection method
        client.text_parts.append("Hello ")
        client.text_parts.append("World!")

        assert client.get_output() == "Hello World!"

    async def test_client_get_chunks(self):
        """Test get_chunks returns copy of chunks."""
        from app.progenitor.runtime.copilot_acp import ProgenitorACPClient

        client = ProgenitorACPClient(work_item_id="test")
        client.chunks.append({"type": "test", "data": "value"})

        chunks = client.get_chunks()
        assert len(chunks) == 1
        assert chunks[0]["type"] == "test"

        # Verify it's a copy
        chunks.append({"type": "new"})
        assert len(client.chunks) == 1

    async def test_client_has_error(self):
        """Test has_error reflects error state."""
        from app.progenitor.runtime.copilot_acp import ProgenitorACPClient

        client = ProgenitorACPClient(work_item_id="test")
        assert not client.has_error()

        client.errors.append("Something went wrong")
        assert client.has_error()


class TestACPLogCapture(unittest.IsolatedAsyncioTestCase):
    """Test ACPLogCapture file output."""

    async def asyncSetUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())

    async def test_log_dir_created(self):
        """Test that log directory is created on init."""
        from app.progenitor.runtime.copilot_acp import ACPLogCapture

        capture = ACPLogCapture(self.temp_dir)

        assert capture.logs_dir.exists()
        assert capture.logs_dir == self.temp_dir / "copilot_acp"

    async def test_generate_log_path(self):
        """Test log path generation."""
        from app.progenitor.runtime.copilot_acp import ACPLogCapture

        capture = ACPLogCapture(self.temp_dir)
        path = capture._generate_log_path("work_item_123")

        assert path.parent == capture.logs_dir
        assert "work_item_123" in path.name
        assert path.suffix == ".log"

    async def test_write_log_creates_file(self):
        """Test that write_log creates a log file."""
        from app.progenitor.runtime.copilot_acp import ACPLogCapture

        capture = ACPLogCapture(self.temp_dir)

        result = {
            "success": True,
            "stdout": "Build completed successfully",
            "stderr": "",
            "stop_reason": "end_turn",
            "chunks": [{"type": "test"}],
            "tool_calls": [{"name": "edit", "id": "1"}],
            "elapsed_seconds": 5.5,
        }

        log_path = capture.write_log("test_build", result)

        assert log_path.exists()
        content = log_path.read_text()
        assert "test_build" in content
        assert "Build completed successfully" in content
        assert "Success: True" in content

    async def test_write_log_handles_errors(self):
        """Test that write_log includes error info."""
        from app.progenitor.runtime.copilot_acp import ACPLogCapture

        capture = ACPLogCapture(self.temp_dir)

        result = {
            "success": False,
            "stdout": "",
            "stderr": "Connection failed",
            "stop_reason": "error",
            "chunks": [],
            "tool_calls": [],
            "elapsed_seconds": 1.0,
        }

        log_path = capture.write_log("error_build", result)

        content = log_path.read_text()
        assert "Connection failed" in content
        assert "Success: False" in content


class TestCopilotCLIWithACP(unittest.IsolatedAsyncioTestCase):
    """Test CopilotCLI with ACP manager."""

    async def asyncSetUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.copilot_cmd = "copilot"
        self.default_flags = ["--yolo"]
        self.model = "test-model"

    async def test_copilot_cli_creates_manager_from_config(self):
        """Test that CopilotCLI creates manager from config."""
        from app.progenitor.tools.copilot import CopilotCLI

        cli = CopilotCLI(
            copilot_cmd=self.copilot_cmd,
            model=self.model,
            default_flags=self.default_flags,
            timeout=1200,
        )

        assert cli.acp_manager is not None
        assert cli.default_timeout == 1200

    async def test_copilot_cli_uses_provided_manager(self):
        """Test that CopilotCLI uses provided manager."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager
        from app.progenitor.tools.copilot import CopilotCLI

        mock_manager = MagicMock(spec=CopilotACPManager)

        cli = CopilotCLI(
            copilot_cmd=self.copilot_cmd,
            model=self.model,
            default_flags=self.default_flags,
            acp_manager=mock_manager,
            timeout=1200,
        )

        assert cli.acp_manager == mock_manager

    async def test_copilot_cli_uses_defaults(self):
        """Test that CopilotCLI uses provided defaults."""
        from app.progenitor.tools.copilot import CopilotCLI

        cli = CopilotCLI(
            copilot_cmd=self.copilot_cmd,
            model=self.model,
            default_flags=self.default_flags,
        )

        assert cli.model == "test-model"
        assert "--yolo" in cli.default_flags

    async def test_build_calls_acp_manager(self):
        """Test that build() uses ACP manager."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager
        from app.progenitor.tools.copilot import CopilotCLI

        mock_manager = AsyncMock(spec=CopilotACPManager)
        mock_manager.prompt = AsyncMock(return_value={
            "success": True,
            "stdout": "Build output",
            "stderr": "",
            "stop_reason": "end_turn",
            "chunks": [{"type": "test"}],
            "tool_calls": [],
            "elapsed_seconds": 2.5,
        })

        cli = CopilotCLI(
            copilot_cmd=self.copilot_cmd,
            model=self.model,
            default_flags=self.default_flags,
            acp_manager=mock_manager,
        )

        result = await cli.build(
            work_item_id="test_build",
            worktree_path=self.temp_dir,
            prompt="Build me an agent",
        )

        mock_manager.prompt.assert_called_once()
        assert result["success"] is True

    async def test_build_handles_error(self):
        """Test that build() handles ACP errors."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager
        from app.progenitor.tools.copilot import CopilotCLI

        mock_manager = AsyncMock(spec=CopilotACPManager)
        mock_manager.prompt = AsyncMock(return_value={
            "success": False,
            "stdout": "",
            "stderr": "ACP connection failed",
            "stop_reason": "error",
            "chunks": [],
            "tool_calls": [],
            "elapsed_seconds": 1.0,
            "error": "Connection refused",
        })

        cli = CopilotCLI(
            copilot_cmd=self.copilot_cmd,
            model=self.model,
            default_flags=self.default_flags,
            acp_manager=mock_manager,
        )

        result = await cli.build(
            work_item_id="error_build",
            worktree_path=self.temp_dir,
            prompt="Build me an agent",
        )

        assert result["success"] is False


class TestCopilotCLISummaryExtraction(unittest.IsolatedAsyncioTestCase):
    """Test summary extraction from copilot output."""

    async def asyncSetUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.copilot_cmd = "copilot"

    async def test_extract_summary_from_output(self):
        """Test extracting summary section from output."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager
        from app.progenitor.tools.copilot import CopilotCLI

        mock_manager = MagicMock(spec=CopilotACPManager)
        cli = CopilotCLI(copilot_cmd=self.copilot_cmd, acp_manager=mock_manager)

        output = """Some output here
More output
Total usage: 1000 tokens
Duration: 5 seconds
Files changed: 3
"""
        summary = cli._extract_summary_from_output(output)

        assert "Total usage" in summary
        assert "Duration" in summary
        assert "Files changed" in summary

    async def test_extract_summary_no_summary(self):
        """Test extracting summary when none present."""
        from app.progenitor.runtime.copilot_acp import CopilotACPManager
        from app.progenitor.tools.copilot import CopilotCLI

        mock_manager = MagicMock(spec=CopilotACPManager)
        cli = CopilotCLI(copilot_cmd=self.copilot_cmd, acp_manager=mock_manager)

        output = "Just some regular output\nNo summary section"
        summary = cli._extract_summary_from_output(output)

        assert summary == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
