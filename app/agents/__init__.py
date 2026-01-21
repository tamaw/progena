"""
A2A Agents Package

This package contains A2A (Agent-to-Agent) protocol compliant agents
that can be invoked by the Progenitor system.
"""

from .hello_world_agent import HelloWorldAgent, hello_world

__all__ = ["HelloWorldAgent", "hello_world"]
