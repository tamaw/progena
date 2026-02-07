"""Mock LLM Client for demonstrations and testing.

EN-004: Add `PROGENITOR_MOCK_LLM=true` for demos without API access.

This mock client provides realistic responses without requiring
actual API credentials. Useful for:
- Demos without Azure/OpenAI credentials
- Local development and testing
- CI/CD pipelines

Usage:
    # Via environment variable
    export PROGENITOR_MOCK_LLM=true

    # Or programmatically
    from app.progenitor.llm.mock_client import MockLLMClient
    client = MockLLMClient()
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import AsyncIterator
from typing import Any

from ..protocols import LLMClientProtocol

logger = logging.getLogger(__name__)


def is_mock_mode_enabled() -> bool:
    """Check if mock LLM mode is enabled via environment variable."""
    return os.environ.get("PROGENITOR_MOCK_LLM", "").lower() in ("true", "1", "yes")


class MockLLMClient(LLMClientProtocol):
    """Mock LLM client that returns realistic fake responses.

    Simulates API latency and returns contextually appropriate
    mock responses for different operations.
    """

    # Simulated latency range in seconds
    MIN_LATENCY = 0.1
    MAX_LATENCY = 0.5

    # Simulated typing speed for streaming (chars per second)
    STREAM_SPEED = 150

    def __init__(
        self,
        latency_range: tuple[float, float] = (0.1, 0.5),
        stream_speed: int = 150,
        fail_rate: float = 0.0,
    ):
        """Initialize mock client.

        Args:
            latency_range: Min/max simulated latency in seconds.
            stream_speed: Simulated typing speed (chars per second).
            fail_rate: Probability of simulated failure (0.0 to 1.0).
        """
        self.latency_range = latency_range
        self.stream_speed = stream_speed
        self.fail_rate = fail_rate
        self._call_count = 0
        logger.info("MockLLMClient initialized (demo mode)")

    async def _simulate_latency(self) -> None:
        """Simulate API call latency."""
        delay = random.uniform(*self.latency_range)
        await asyncio.sleep(delay)

    def _maybe_fail(self) -> None:
        """Potentially raise an error to simulate failures."""
        if self.fail_rate > 0 and random.random() < self.fail_rate:
            raise RuntimeError("Simulated API failure (mock mode)")

    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Return a mock chat response."""
        self._call_count += 1
        await self._simulate_latency()
        self._maybe_fail()

        # Extract user message for context
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # Generate contextual response
        response = self._generate_contextual_response(user_message, system_prompt)

        logger.debug("Mock chat response (call %d): %s...", self._call_count, response[:100])
        return response

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a mock chat response."""
        self._call_count += 1
        await self._simulate_latency()
        self._maybe_fail()

        # Get full response
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        response = self._generate_contextual_response(user_message, system_prompt)

        # Stream it character by character with realistic timing
        delay_per_char = 1.0 / self.stream_speed
        for char in response:
            yield char
            await asyncio.sleep(delay_per_char)

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> str:
        """Return a mock completion response."""
        self._call_count += 1
        await self._simulate_latency()
        self._maybe_fail()

        return self._generate_contextual_response(prompt, system_prompt)

    async def decide(
        self,
        user_input: str,
        conversation_history: list[dict[str, Any]],
        tool_context: str,
    ) -> dict[str, Any]:
        """Return a mock decision."""
        self._call_count += 1
        await self._simulate_latency()
        self._maybe_fail()

        # Analyze input for keywords
        input_lower = user_input.lower()

        # Determine action based on keywords
        if any(word in input_lower for word in ["create", "build", "make", "implement"]):
            action = "execute"
            reasoning = "User wants to create something - executing build action."
        elif any(word in input_lower for word in ["what", "how", "why", "explain"]):
            action = "respond"
            reasoning = "User is asking a question - providing direct response."
        elif any(word in input_lower for word in ["plan", "schedule", "organize"]):
            action = "plan"
            reasoning = "User wants planning - creating work items."
        elif any(word in input_lower for word in ["help", "list", "show"]):
            action = "respond"
            reasoning = "User needs information - responding directly."
        else:
            action = "respond"
            reasoning = "General query - providing helpful response."

        return {
            "action": action,
            "reasoning": reasoning,
            "confidence": round(random.uniform(0.75, 0.95), 2),
            "requires_tools": action == "execute",
            "suggested_capabilities": [],
        }

    async def plan(
        self,
        user_input: str,
        decision: dict[str, Any],
        current_backlog: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return mock work items."""
        self._call_count += 1
        await self._simulate_latency()
        self._maybe_fail()

        # Generate 1-3 mock work items
        num_items = random.randint(1, 3)
        items = []

        for i in range(num_items):
            items.append({
                "title": f"Work item {i + 1}: {user_input[:30]}...",
                "description": f"Automatically generated work item for: {user_input}",
                "priority": random.choice(["high", "medium", "low"]),
                "estimated_effort": random.choice(["small", "medium", "large"]),
                "status": "pending",
            })

        return items

    async def generate_build_spec(
        self,
        work_item: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a mock build specification."""
        self._call_count += 1
        await self._simulate_latency()
        self._maybe_fail()

        return {
            "work_item_id": work_item.get("id", "mock-item"),
            "steps": [
                {"action": "analyze", "description": "Analyze requirements"},
                {"action": "implement", "description": "Implement solution"},
                {"action": "verify", "description": "Verify results"},
            ],
            "estimated_duration": "5 minutes",
            "dependencies": [],
            "outputs": ["result"],
        }

    async def verify_execution(
        self,
        work_item: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a mock verification result."""
        self._call_count += 1
        await self._simulate_latency()
        self._maybe_fail()

        # High success rate in mock mode
        success = random.random() > 0.1

        return {
            "success": success,
            "score": round(random.uniform(0.8, 1.0) if success else random.uniform(0.3, 0.6), 2),
            "feedback": "Execution completed successfully." if success else "Some issues detected.",
            "suggestions": [] if success else ["Review output", "Check inputs"],
        }

    async def generate_response(
        self,
        user_input: str,
        context: dict[str, Any],
    ) -> str:
        """Generate a mock user-facing response."""
        self._call_count += 1
        await self._simulate_latency()
        self._maybe_fail()

        return self._generate_contextual_response(user_input, None)

    async def summarize(
        self,
        content: str,
        max_length: int = 200,
    ) -> str:
        """Return a mock summary."""
        self._call_count += 1
        await self._simulate_latency()
        self._maybe_fail()

        # Simple truncation-based "summary"
        if len(content) <= max_length:
            return content

        return content[:max_length - 20] + "... [summarized]"

    def _generate_contextual_response(
        self,
        user_input: str,
        system_prompt: str | None = None,
    ) -> str:
        """Generate a contextually appropriate mock response."""
        input_lower = user_input.lower() if user_input else ""

        # Check for specific patterns
        if any(word in input_lower for word in ["hello", "hi", "hey"]):
            return "Hello! I'm the Progenitor AI assistant (running in demo mode). How can I help you today?"

        if any(word in input_lower for word in ["help", "what can you do"]):
            return (
                "I'm Progenitor, an AI assistant that can help you with:\n"
                "- Answering questions and providing information\n"
                "- Creating and managing tasks\n"
                "- Building and executing plans\n"
                "- Code generation and analysis\n\n"
                "Note: I'm currently running in demo mode without live API access."
            )

        if "weather" in input_lower:
            return "I'm running in demo mode and don't have access to real-time weather data. In production, I could help with weather information!"

        if any(word in input_lower for word in ["code", "program", "function"]):
            return (
                "Here's a simple example (demo mode):\n\n"
                "```python\n"
                "def hello_world():\n"
                "    print('Hello from Progenitor!')\n"
                "```\n\n"
                "In production mode, I can generate more complex code based on your requirements."
            )

        if any(word in input_lower for word in ["create", "build", "make"]):
            return (
                "I understand you want to create something. In demo mode, I'll simulate the process:\n\n"
                "1. ✓ Analyzing your request\n"
                "2. ✓ Creating a plan\n"
                "3. ✓ Executing the build\n"
                "4. ✓ Verifying results\n\n"
                "Task completed successfully (simulated)."
            )

        # Default response
        return (
            f"I've processed your request: \"{user_input[:50]}{'...' if len(user_input) > 50 else ''}\"\n\n"
            "This is a demo mode response. In production with live API access, "
            "I would provide a more detailed and contextual answer based on your specific needs."
        )

    def get_stats(self) -> dict[str, Any]:
        """Get mock client statistics."""
        return {
            "mode": "mock",
            "call_count": self._call_count,
            "latency_range": self.latency_range,
            "stream_speed": self.stream_speed,
            "fail_rate": self.fail_rate,
        }


def create_llm_client(
    *args,
    force_mock: bool = False,
    **kwargs,
) -> LLMClientProtocol:
    """Factory function to create appropriate LLM client.

    Automatically selects MockLLMClient if:
    - PROGENITOR_MOCK_LLM=true environment variable is set
    - force_mock=True is passed

    Otherwise creates the real LLMClient.

    Args:
        *args: Passed to LLMClient constructor.
        force_mock: Force mock mode regardless of environment.
        **kwargs: Passed to LLMClient constructor.

    Returns:
        LLM client implementation (MockLLMClient or LLMClient).
    """
    if force_mock or is_mock_mode_enabled():
        logger.info("Using MockLLMClient (demo mode)")
        return MockLLMClient()  # type: ignore[return-value]

    # Import real client only when needed
    from .client import LLMClient
    return LLMClient(*args, **kwargs)  # type: ignore[return-value]


__all__ = [
    "MockLLMClient",
    "create_llm_client",
    "is_mock_mode_enabled",
]
