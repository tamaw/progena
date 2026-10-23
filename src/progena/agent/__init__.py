"""
DeepAgent integration layer for Progena.

This package provides the bridge between Progena's existing architecture
and the LangChain DeepAgent harness, selectively adopting DeepAgent's
middleware, skills, memory, and context management while preserving
Progena's superior inner build loop, scheduling, and A2A protocol.

Modules:
    agent: Main factory — create_progena_agent()
    adapters: ChatOpenAI adapter, runtime graph adapter, and DeepAgent tool wrappers
    backends: CompositeBackend (LocalShell + Store + State)
    middleware/: One module per custom middleware:
        async_subagent_tracking, audit_logging, direct_a2a_fast_path,
        dynamic_mcp, schema_compaction, and secret_redaction
    object_agent_manifest: Object-agent manifest parsing and discovery
    object_agents: Object-agent construction and lazy invocation
    async_subagents: Background async subagent descriptors
"""
