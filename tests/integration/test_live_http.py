"""
Live integration tests for Progenitor via HTTP + SSE.

These tests communicate with a live Progenitor instance through its runtime
HTTP server, exercising real functionality. Results are written to files for
manual review rather than using pass/fail assertions.

Test Coverage:
- Chat: Basic user message and response
- Schedule: Scheduled task creation
- Build: Agent building workflow
- Tool Call: System tool invocation
- MCP: MCP tool usage
"""

import asyncio
import contextlib
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.integration.helpers import (
    DEFAULT_BASE_URL,
    is_progenitor_running,
    iter_chat_sse_frames,
    send_message_and_collect,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TIMEOUT = 120.0  # 2 minute timeout for real agent tests
RESULTS_DIR = Path(__file__).parent / "results"

# Ensure results directory exists
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def write_result(test_name: str, data: dict[str, Any]) -> None:
    """Write test result to a JSON file for manual review."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = RESULTS_DIR / f"{test_name}_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Test result written to: {filename}")


@pytest.fixture
async def http_client():
    """Create an httpx client connected to a live Progenitor runtime."""
    if not await is_progenitor_running():
        pytest.skip("Could not connect to Progenitor runtime HTTP server. Make sure it's running.")

    async with httpx.AsyncClient(
        base_url=DEFAULT_BASE_URL, timeout=httpx.Timeout(10.0, read=None)
    ) as client:
        yield client


@pytest.mark.integration
async def test_chat_basic(http_client):
    """Test basic chat functionality using streaming mode."""
    message = "What is 2 + 2? Please answer concisely."

    result = await send_message_and_collect(http_client, message, "chat_basic")

    write_result("chat_basic", result)

    if result["success"]:
        logger.info(f"✓ Chat test completed in {result['duration_seconds']:.2f}s")
    else:
        logger.error(f"✗ Chat test failed: {result['error']}")


@pytest.mark.integration
async def test_chat_streaming(http_client):
    """Test streaming chat functionality."""
    message = "List three programming languages. Be brief."

    result = await send_message_and_collect(http_client, message, "chat_streaming", timeout=30.0)

    write_result("chat_streaming", result)

    if result["success"]:
        logger.info(
            f"✓ Streaming test completed in {result['duration_seconds']:.2f}s "
            f"({len(result['chunks'])} chunks)"
        )
    else:
        logger.error(f"✗ Streaming test failed: {result['error']}")


@pytest.mark.integration
async def test_schedule_task(http_client):
    """Test schedule task creation and execution.

    Schedules a task for 1 minute in the future, waits, then asks the agent
    if it executed. Pure black-box test via HTTP only.
    """
    now = datetime.now(UTC)
    scheduled_time = now + timedelta(minutes=1)
    time_str = scheduled_time.strftime("%H:%M")  # HH:MM format

    logger.info(f"Current time: {now.strftime('%H:%M:%S')} UTC")
    logger.info(f"Scheduling task for: {time_str} UTC (1 minute from now)")

    message = (
        f"Schedule a task to run at {time_str} UTC today: "
        "'Reply with the message: INTEGRATION_TEST_COMPLETED'."
    )

    result = await send_message_and_collect(http_client, message, "schedule_task_create")

    write_result("schedule_task_create", result)

    if result["success"]:
        logger.info(f"✓ Task scheduled in {result['duration_seconds']:.2f}s")
        logger.info("  Waiting 80 seconds for task to trigger...")

        # Wait 80 seconds (1 minute + 20 seconds buffer for scheduler polling)
        await asyncio.sleep(80)

        logger.info("  Checking if scheduled task executed...")
        check_message = (
            "Did any scheduled tasks just run in the last 2 minutes? If so, what was the result?"
        )

        check_result = await send_message_and_collect(
            http_client, check_message, "schedule_task_verify"
        )

        write_result("schedule_task_verify", check_result)

        if check_result["success"]:
            logger.info(f"✓ Verification completed in {check_result['duration_seconds']:.2f}s")
            if (
                "INTEGRATION_TEST_COMPLETED" in check_result["response"]
                or "integration" in check_result["response"].lower()
            ):
                logger.info("  ✓ Scheduled task appears to have executed!")
            else:
                logger.warning("  ⚠️ Could not confirm task execution from agent response")
        else:
            logger.error(f"✗ Verification failed: {check_result['error']}")
    else:
        logger.error(f"✗ Schedule task creation failed: {result['error']}")


@pytest.mark.integration
async def test_build_request(http_client):
    """Test build/agent creation workflow (not full execution)."""
    message = (
        "I need a simple agent that can check if a file exists and return true/false. "
        "Can you help me understand how you would build this?"
    )

    result = await send_message_and_collect(http_client, message, "build_request")

    write_result("build_request", result)

    if result["success"]:
        logger.info(f"✓ Build request test completed in {result['duration_seconds']:.2f}s")
    else:
        logger.error(f"✗ Build request test failed: {result['error']}")


@pytest.mark.integration
async def test_system_tool(http_client):
    """Test system tool invocation."""
    message = "What agents are currently available? Please list them."

    result = await send_message_and_collect(http_client, message, "system_tool")

    write_result("system_tool", result)

    if result["success"]:
        logger.info(f"✓ System tool test completed in {result['duration_seconds']:.2f}s")
    else:
        logger.error(f"✗ System tool test failed: {result['error']}")


async def _collect_broadcast_events(
    client: httpx.AsyncClient, events: list[dict[str, Any]]
) -> None:
    """Background task: append every ``/v1/events`` broadcast frame to ``events``."""
    async with client.stream("GET", "/v1/events") as response:
        async for event_name, data in iter_chat_sse_frames(response):
            if event_name == "agent_event":
                events.append(data)


@pytest.mark.integration
async def test_mcp_tool(http_client):
    """Test MCP tool usage - calls the primer tool to get session context."""
    events: list[dict[str, Any]] = []
    listener = asyncio.create_task(_collect_broadcast_events(http_client, events))

    # Strongly request the tool call so the model is likely to emit a tool_call item.
    message = (
        "Call the MCP tool 'primer' now. Do not answer until after the tool returns. "
        "Then summarize what the tool returned in 1-2 sentences."
    )

    result = await send_message_and_collect(http_client, message, "mcp_tool")

    # Best-effort: give trailing events near the end of streaming a moment to arrive.
    await asyncio.sleep(0.5)

    listener.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(listener, timeout=5.0)

    result["events"] = events
    result["mcp_tool_events"] = [
        e for e in events if str(e.get("event_type", "")).startswith("mcp_tool_")
    ]
    result["observed_tool_call"] = any(
        e.get("event_type") == "mcp_tool_call_started" for e in events
    )

    write_result("mcp_tool", result)

    if result["success"]:
        logger.info(f"✓ MCP tool test completed in {result['duration_seconds']:.2f}s")
        response_lower = result["response"].lower()

        if "not available" in response_lower or "can't" in response_lower:
            logger.warning("  ⚠️ MCP tools not available - check configuration")
        elif any(
            keyword in response_lower
            for keyword in ["time", "location", "session", "context", "primer"]
        ):
            logger.info("  ✓ Response contains primer/session context info (MCP likely working)")
        else:
            logger.info("  Response received (check results for MCP usage)")
    else:
        logger.error(f"✗ MCP tool test failed: {result['error']}")


@pytest.mark.integration
async def test_multi_turn_conversation(http_client):
    """Test multi-turn conversation (state persistence)."""
    test_name = "multi_turn"
    results = []

    # Turn 1
    message1 = "My name is TestUser. Remember this."
    result1 = await send_message_and_collect(http_client, message1, f"{test_name}_turn1")
    results.append(result1)

    # Turn 2 - reference previous context
    message2 = "What is my name?"
    result2 = await send_message_and_collect(http_client, message2, f"{test_name}_turn2")
    results.append(result2)

    combined = {
        "test_name": test_name,
        "timestamp": datetime.now().isoformat(),
        "turns": results,
        "success": all(r["success"] for r in results),
    }

    write_result("multi_turn", combined)

    if combined["success"]:
        logger.info(f"✓ Multi-turn test completed ({len(results)} turns)")
    else:
        logger.error("✗ Multi-turn test failed")
