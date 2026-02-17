"""Tests for agent discovery functionality."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.tools.agent_discovery import (
    SKIP_FILES,
    SKIP_FOLDERS,
    AgentDiscovery,
    AgentFolderInfo,
    DiscoveredAgentSpec,
)


class TestAgentFolderInfo:
    """Tests for AgentFolderInfo dataclass."""

    def test_to_prompt_string(self):
        """Test conversion to LLM prompt format."""
        info = AgentFolderInfo(
            folder_name="test_agent",
            folder_path=Path("build/agents/test_agent"),
            files=["agent.py", "README.md"],
            file_contents={"agent.py": "print('hello')"},
        )
        result = info.to_prompt_string()
        assert "## Agent Folder: test_agent" in result
        assert "Files: agent.py, README.md" in result
        assert "print('hello')" in result

    def test_to_prompt_string_truncates_long_content(self):
        """Test that long file contents are truncated."""
        long_content = "x" * 5000
        info = AgentFolderInfo(
            folder_name="test_agent",
            folder_path=Path("build/agents/test_agent"),
            files=["agent.py"],
            file_contents={"agent.py": long_content},
        )
        result = info.to_prompt_string()
        assert "... (truncated)" in result
        assert len(result) < 5000


class TestDiscoveredAgentSpec:
    """Tests for DiscoveredAgentSpec model."""

    def test_default_values(self):
        """Test default values are set correctly."""
        spec = DiscoveredAgentSpec(
            name="test",
            description="Test agent",
            entrypoint="agent.py",
        )
        assert spec.transport == "http"
        assert spec.version == "0.1.0"
        assert spec.is_valid_agent is True
        assert spec.reason_if_invalid is None

    def test_with_capabilities(self):
        """Test spec with capabilities."""
        spec = DiscoveredAgentSpec(
            name="test",
            description="Test agent",
            capabilities=["greeting", "file_ops"],
            entrypoint="http_agent.py",
            transport="http",
        )
        assert spec.capabilities == ["greeting", "file_ops"]


class TestAgentDiscovery:
    """Tests for AgentDiscovery class."""

    @pytest.fixture
    def mock_llm_client(self):
        """Create a mock LLM client."""
        client = AsyncMock()
        client.chat = AsyncMock(return_value='{"agents": []}')
        return client

    @pytest.fixture
    def mock_registry(self):
        """Create a mock registry."""
        registry = MagicMock()
        registry.allocate_port = MagicMock(return_value=8100)
        registry.register_agent = AsyncMock()
        return registry

    def test_scan_empty_directory(self, mock_llm_client, mock_registry, tmp_path):
        """Test scanning an empty directory."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm_client,
            registry=mock_registry,
        )
        folders = discovery.scan_agents_folder()
        assert folders == []

    def test_scan_skips_pycache(self, mock_llm_client, mock_registry, tmp_path):
        """Test that __pycache__ folders are skipped."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        pycache = agents_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "test.pyc").write_text("compiled")

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm_client,
            registry=mock_registry,
        )
        folders = discovery.scan_agents_folder()
        assert folders == []

    def test_scan_finds_agent_folder(self, mock_llm_client, mock_registry, tmp_path):
        """Test that valid agent folders are found."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        agent_folder = agents_dir / "hello_world"
        agent_folder.mkdir()
        (agent_folder / "agent.py").write_text('"""Hello agent."""\nprint("hello")')

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm_client,
            registry=mock_registry,
        )
        folders = discovery.scan_agents_folder()

        assert len(folders) == 1
        assert folders[0].folder_name == "hello_world"
        assert "agent.py" in folders[0].files
        assert "agent.py" in folders[0].file_contents

    def test_fallback_extraction_with_http_agent(self, mock_llm_client, mock_registry, tmp_path):
        """Test fallback extraction prefers http_agent.py."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        agent_folder = agents_dir / "test_agent"
        agent_folder.mkdir()
        (agent_folder / "agent.py").write_text("print('stdio')")
        (agent_folder / "http_agent.py").write_text('"""HTTP Agent."""\nfrom aiohttp import web')

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm_client,
            registry=mock_registry,
        )
        folders = discovery.scan_agents_folder()
        result = discovery._fallback_extraction(folders)

        assert len(result.agents) == 1
        agent = result.agents[0]
        assert agent.name == "test_agent"
        assert agent.entrypoint == "http_agent.py"
        assert agent.transport == "http"

    def test_fallback_extraction_detects_http_in_content(self, mock_llm_client, mock_registry, tmp_path):
        """Test fallback extraction detects HTTP from content."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        agent_folder = agents_dir / "my_agent"
        agent_folder.mkdir()
        (agent_folder / "agent.py").write_text('"""My Agent."""\nfrom aiohttp import web\nclass MyAgent: pass')

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm_client,
            registry=mock_registry,
        )
        folders = discovery.scan_agents_folder()
        result = discovery._fallback_extraction(folders)

        assert len(result.agents) == 1
        assert result.agents[0].transport == "http"

    def test_fallback_extraction_extracts_docstring(self, mock_llm_client, mock_registry, tmp_path):
        """Test fallback extraction gets description from docstring."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        agent_folder = agents_dir / "doc_agent"
        agent_folder.mkdir()
        (agent_folder / "agent.py").write_text('"""This is a greeting agent that says hello."""\nclass Agent: pass')

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm_client,
            registry=mock_registry,
        )
        folders = discovery.scan_agents_folder()
        result = discovery._fallback_extraction(folders)

        assert len(result.agents) == 1
        assert "greeting agent" in result.agents[0].description.lower()


class TestSkipPatterns:
    """Test skip folder and file patterns."""

    def test_skip_folders_includes_common_patterns(self):
        """Test that common skip patterns are included."""
        assert "__pycache__" in SKIP_FOLDERS
        assert ".git" in SKIP_FOLDERS
        assert "base" in SKIP_FOLDERS
        assert "node_modules" in SKIP_FOLDERS

    def test_skip_files_includes_common_patterns(self):
        """Test that common skip files are included."""
        assert "__init__.py" in SKIP_FILES
        assert ".gitkeep" in SKIP_FILES


class TestAgentDiscoveryIntegration:
    """Integration tests for agent discovery."""

    @pytest.mark.asyncio
    async def test_discover_and_register_no_agents(self, tmp_path):
        """Test discovery with no agent folders."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        mock_llm = AsyncMock()
        mock_registry = AsyncMock()

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm,
            registry=mock_registry,
        )
        result = await discovery.discover_and_register()

        assert result["success"] is True
        assert result["agents_found"] == 0
        assert result["agents_registered"] == 0
