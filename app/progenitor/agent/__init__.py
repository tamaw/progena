"""
DeepAgent integration layer for Progenitor.

This package provides the bridge between Progenitor's existing architecture
and the LangChain DeepAgent harness, selectively adopting DeepAgent's
middleware, skills, memory, and context management while preserving
Progenitor's superior inner build loop, scheduling, and A2A protocol.

Modules:
    deep_agent: Main factory — create_progenitor_agent()
    langchain_adapter: ChatOpenAI adapter for configured endpoint
    tools_adapter: BaseSystemTool → DeepAgent tool wrappers
    backends: CompositeBackend (LocalShell + Store + State)
    middleware: SecretRedaction, AuditLogging, Metrics
    subagents: Build loop, code reviewer, researcher
"""

from .deep_agent import create_progenitor_agent

__all__ = ["create_progenitor_agent"]
