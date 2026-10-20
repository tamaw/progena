"""Tests for agent discovery functionality."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.registry.discovery import AgentDiscovery, DiscoveredAgentSpec


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

    def test_fallback_extraction_detects_http_in_content(
        self, mock_llm_client, mock_registry, tmp_path
    ):
        """Test fallback extraction detects HTTP from content."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        agent_folder = agents_dir / "my_agent"
        agent_folder.mkdir()
        (agent_folder / "agent.py").write_text(
            '"""My Agent."""\nfrom aiohttp import web\nclass MyAgent: pass'
        )

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
        (agent_folder / "agent.py").write_text(
            '"""This is a greeting agent that says hello."""\nclass Agent: pass'
        )

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm_client,
            registry=mock_registry,
        )
        folders = discovery.scan_agents_folder()
        result = discovery._fallback_extraction(folders)

        assert len(result.agents) == 1
        assert "greeting agent" in result.agents[0].description.lower()


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

    @pytest.mark.asyncio
    async def test_register_agent_uses_explicit_url(self, tmp_path):
        """Discovery should register explicit endpoints without allocating ports."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        mock_llm = AsyncMock()
        mock_registry = MagicMock()
        mock_registry.register_agent = AsyncMock()

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm,
            registry=mock_registry,
        )
        spec = DiscoveredAgentSpec(
            name="builder_agent",
            description="External builder",
            entrypoint="agent.py",
            capabilities=["build_progenitor"],
            url="http://builder:8107/",
        )

        await discovery._register_agent(spec)

        entry = mock_registry.register_agent.await_args.args[0]
        assert entry.url == "http://builder:8107"
        assert entry.port is None
        assert entry.agent_card_url == "http://builder:8107/.well-known/agent-card.json"

    @pytest.mark.asyncio
    async def test_register_agent_without_endpoint_does_not_allocate_port(self, tmp_path):
        """Discovery records metadata only when an agent has no declared endpoint."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        mock_llm = AsyncMock()
        mock_registry = MagicMock()
        mock_registry.register_agent = AsyncMock()

        discovery = AgentDiscovery(
            agents_dir=agents_dir,
            llm_client=mock_llm,
            registry=mock_registry,
        )
        spec = DiscoveredAgentSpec(
            name="metadata_only",
            description="Agent with no declared runtime endpoint",
            entrypoint="agent.py",
        )

        await discovery._register_agent(spec)

        entry = mock_registry.register_agent.await_args.args[0]
        assert entry.url is None
        assert entry.port is None
        assert entry.agent_card_url is None
