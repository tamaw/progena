"""
Hello World A2A Agent

A simple Agent-to-Agent (A2A) protocol compliant agent
that responds with "Hello World!" when invoked.
"""

import json
from typing import Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class A2AMessage:
    """Standard A2A message format."""
    role: str  # "user", "assistant", "system"
    content: str
    metadata: Optional[dict] = None


@dataclass
class A2ARequest:
    """A2A protocol request format."""
    messages: list[A2AMessage]
    agent_id: str = "hello_world_agent"
    context: Optional[dict] = None


@dataclass
class A2AResponse:
    """A2A protocol response format."""
    message: A2AMessage
    agent_id: str = "hello_world_agent"
    status: str = "success"
    metadata: Optional[dict] = None


class HelloWorldAgent:
    """
    A simple Hello World A2A agent.

    This agent responds to any message with "Hello World!".
    It demonstrates the basic A2A protocol compliance.
    """

    AGENT_ID = "hello_world_agent"
    AGENT_NAME = "Hello World Agent"
    DESCRIPTION = "A simple agent that responds with Hello World!"
    CAPABILITIES = ["greeting", "hello_world"]

    def __init__(self):
        """Initialize the Hello World agent."""
        self.name = self.AGENT_NAME
        self.capabilities = self.CAPABILITIES

    async def invoke(self, request: A2ARequest) -> A2AResponse:
        """
        Handle an A2A invocation request.

        Args:
            request: The A2A request containing messages and context.

        Returns:
            A2AResponse with the "Hello World!" greeting.
        """
        response_message = A2AMessage(
            role="assistant",
            content="Hello World!",
            metadata={"agent": self.AGENT_ID}
        )

        return A2AResponse(
            message=response_message,
            agent_id=self.AGENT_ID,
            status="success",
            metadata={
                "agent_name": self.AGENT_NAME,
                "capabilities": self.CAPABILITIES,
            }
        )

    def get_agent_card(self) -> dict:
        """
        Return the agent's capability card for discovery.

        Returns:
            Dict with agent metadata for registry.
        """
        return {
            "agent_id": self.AGENT_ID,
            "name": self.AGENT_NAME,
            "description": self.DESCRIPTION,
            "capabilities": self.CAPABILITIES,
            "protocol": "a2a",
            "version": "1.0.0",
        }


# Convenience function for direct invocation
async def hello_world(message: str = "") -> str:
    """
    Simple function to get a Hello World response.

    Args:
        message: Optional input message (ignored).

    Returns:
        "Hello World!" string.
    """
    return "Hello World!"


# For testing
if __name__ == "__main__":
    import asyncio

    async def main():
        agent = HelloWorldAgent()

        # Print agent card
        print("Agent Card:")
        print(json.dumps(agent.get_agent_card(), indent=2))
        print()

        # Test invocation
        request = A2ARequest(
            messages=[A2AMessage(role="user", content="Say hello")],
        )

        response = await agent.invoke(request)
        print("Response:")
        print(json.dumps(asdict(response), indent=2))

    asyncio.run(main())
