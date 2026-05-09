"""
DeepAgent integration layer for Progenitor.

This package provides the bridge between Progenitor's existing architecture
and the LangChain DeepAgent harness, selectively adopting DeepAgent's
middleware, skills, memory, and context management while preserving
Progenitor's superior inner build loop, scheduling, and A2A protocol.

Modules:
    agent: Main factory — create_progenitor_agent()
    adapters: ChatOpenAI adapter, runtime graph adapter, and DeepAgent tool wrappers
    backends: CompositeBackend (LocalShell + Store + State)
    middleware: SecretRedaction, AuditLogging, Metrics
    subagents: Progenitor helper subagents
"""
