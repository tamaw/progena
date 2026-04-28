"""
Live integration tests for Progenitor via IPC.

These tests communicate with a live Progenitor instance through IPC,
exercising real functionality. Results are written to files for manual
review rather than using pass/fail assertions.

Test Coverage:
- Chat: Basic user message and response
- Schedule: Scheduled task creation
- Build: Agent building workflow
- Tool Call: System tool invocation
- MCP: MCP tool usage
"""

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.ipc.client import IPCClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Test configuration
IPC_HOST = "127.0.0.1"
IPC_PORT = 9500
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


async def send_message_and_collect(
    client: IPCClient,
    message: str,
    test_name: str,
    timeout: float = TIMEOUT,
) -> dict[str, Any]:
    """Send a message to Progenitor and collect the response.

    Uses streaming mode to collect the full response, matching the working backend pattern.

    Args:
        client: IPC client instance
        message: User message to send
        test_name: Name of the test (for logging)
        timeout: Timeout in seconds

    Returns:
        Dictionary with test results
    """
    logger.info(f"[{test_name}] Sending: {message[:100]}...")

    result = {
        "test_name": test_name,
        "timestamp": datetime.now().isoformat(),
        "request": message,
        "response": "",
        "chunks": [],
        "error": None,
        "duration_seconds": 0,
    }

    start_time = asyncio.get_event_loop().time()

    try:
        # Use streaming mode (matches working backend pattern)
        async for item in client.send_message_stream_events(message):
            if item.get("type") != "stream_chunk":
                continue
            chunk = str(item.get("content", ""))
            if chunk:
                result["chunks"].append(chunk)
                result["response"] += chunk

        result["success"] = True
        logger.info(
            f"[{test_name}] Response received ({len(result['chunks'])} chunks, {len(result['response'])} chars)"
        )

    except TimeoutError:
        result["error"] = f"Timeout after {timeout}s"
        result["success"] = False
        logger.error(f"[{test_name}] Timeout!")

    except Exception as e:
        result["error"] = str(e)
        result["success"] = False
        logger.error(f"[{test_name}] Error: {e}", exc_info=True)

    end_time = asyncio.get_event_loop().time()
    result["duration_seconds"] = end_time - start_time

    return result


async def send_message_stream_and_collect(
    client: IPCClient,
    message: str,
    test_name: str,
    timeout: float = TIMEOUT,
) -> dict[str, Any]:
    """Send a message to Progenitor and collect streaming response.

    Args:
        client: IPC client instance
        message: User message to send
        test_name: Name of the test (for logging)
        timeout: Timeout in seconds

    Returns:
        Dictionary with test results
    """
    logger.info(f"[{test_name}] Sending (stream): {message[:100]}...")

    result = {
        "test_name": test_name,
        "timestamp": datetime.now().isoformat(),
        "request": message,
        "response": "",
        "chunks": [],
        "error": None,
        "duration_seconds": 0,
    }

    start_time = asyncio.get_event_loop().time()

    try:
        async for item in client.send_message_stream_events(message):
            if item.get("type") != "stream_chunk":
                continue
            chunk = str(item.get("content", ""))
            result["chunks"].append(chunk)
            result["response"] += chunk

        result["success"] = True
        logger.info(
            f"[{test_name}] Stream complete ({len(result['chunks'])} chunks, {len(result['response'])} chars)"
        )

    except TimeoutError:
        result["error"] = f"Timeout after {timeout}s"
        result["success"] = False
        logger.error(f"[{test_name}] Timeout!")

    except Exception as e:
        result["error"] = str(e)
        result["success"] = False
        logger.error(f"[{test_name}] Error: {e}", exc_info=True)

    end_time = asyncio.get_event_loop().time()
    result["duration_seconds"] = end_time - start_time

    return result


@pytest.fixture
async def ipc_client():
    """Create and connect an IPC client."""
    client = IPCClient(
        host=IPC_HOST,
        port=IPC_PORT,
        auto_reconnect=False,
    )

    # Try to connect
    connected = await client.connect()
    if not connected:
        pytest.skip("Could not connect to Progenitor IPC server. Make sure it's running.")

    yield client

    # Cleanup
    await client.disconnect()


@pytest.mark.integration
async def test_chat_basic(ipc_client):
    """Test basic chat functionality using streaming mode."""
    message = "What is 2 + 2? Please answer concisely."

    result = await send_message_and_collect(
        ipc_client,
        message,
        "chat_basic",
    )

    write_result("chat_basic", result)

    # Log summary
    if result["success"]:
        logger.info(f"✓ Chat test completed in {result['duration_seconds']:.2f}s")
    else:
        logger.error(f"✗ Chat test failed: {result['error']}")


@pytest.mark.integration
async def test_chat_streaming(ipc_client):
    """Test streaming chat functionality."""
    message = "List three programming languages. Be brief."

    result = await send_message_stream_and_collect(
        ipc_client,
        message,
        "chat_streaming",
        timeout=30.0,
    )

    write_result("chat_streaming", result)

    # Log summary
    if result["success"]:
        logger.info(
            f"✓ Streaming test completed in {result['duration_seconds']:.2f}s ({len(result['chunks'])} chunks)"
        )
    else:
        logger.error(f"✗ Streaming test failed: {result['error']}")


@pytest.mark.integration
async def test_schedule_task(ipc_client):
    """Test schedule task creation and execution.

    Schedules a task for 1 minute in the future, waits, then asks the agent
    if it executed. Pure black-box test via IPC only.
    """
    import asyncio
    from datetime import datetime, timedelta

    # Calculate time 1 minute from now
    now = datetime.now(UTC)
    scheduled_time = now + timedelta(minutes=1)
    time_str = scheduled_time.strftime("%H:%M")  # HH:MM format

    logger.info(f"Current time: {now.strftime('%H:%M:%S')} UTC")
    logger.info(f"Scheduling task for: {time_str} UTC (1 minute from now)")

    # Schedule a task for 1 minute from now
    message = (
        f"Schedule a task to run at {time_str} UTC today: 'Reply with the message: INTEGRATION_TEST_COMPLETED'. "
        "Use the schedule_task tool."
    )

    result = await send_message_and_collect(
        ipc_client,
        message,
        "schedule_task_create",
    )

    write_result("schedule_task_create", result)

    # Log scheduling result
    if result["success"]:
        logger.info(f"✓ Task scheduled in {result['duration_seconds']:.2f}s")
        logger.info("  Waiting 80 seconds for task to trigger...")

        # Wait 80 seconds (1 minute + 20 seconds buffer for scheduler polling)
        await asyncio.sleep(80)

        # Ask the agent if the scheduled task ran
        logger.info("  Checking if scheduled task executed...")
        check_message = (
            "Did any scheduled tasks just run in the last 2 minutes? If so, what was the result?"
        )

        check_result = await send_message_and_collect(
            ipc_client,
            check_message,
            "schedule_task_verify",
        )

        write_result("schedule_task_verify", check_result)

        if check_result["success"]:
            logger.info(f"✓ Verification completed in {check_result['duration_seconds']:.2f}s")
            # Check if the test message appeared in the response
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
async def test_build_request(ipc_client):
    """Test build/agent creation workflow (not full execution)."""
    # Ask about building something - this tests the decide -> plan flow
    message = (
        "I need a simple agent that can check if a file exists and return true/false. "
        "Can you help me understand how you would build this?"
    )

    result = await send_message_and_collect(
        ipc_client,
        message,
        "build_request",
    )

    write_result("build_request", result)

    # Log summary
    if result["success"]:
        logger.info(f"✓ Build request test completed in {result['duration_seconds']:.2f}s")
    else:
        logger.error(f"✗ Build request test failed: {result['error']}")


@pytest.mark.integration
async def test_system_tool(ipc_client):
    """Test system tool invocation."""
    # Ask for agent discovery - this uses the system tool
    message = "What agents are currently available? Please list them."

    result = await send_message_and_collect(
        ipc_client,
        message,
        "system_tool",
    )

    write_result("system_tool", result)

    # Log summary
    if result["success"]:
        logger.info(f"✓ System tool test completed in {result['duration_seconds']:.2f}s")
    else:
        logger.error(f"✗ System tool test failed: {result['error']}")


@pytest.mark.integration
async def test_mcp_tool(ipc_client):
    """Test MCP tool usage - calls the primer tool to get session context."""
    events: list[dict[str, Any]] = []

    async def _on_event(event: dict[str, Any]) -> None:
        events.append(event)

    ipc_client.set_event_callback(_on_event)

    # Strongly request the tool call so the model is likely to emit a tool_call item.
    message = (
        "Call the MCP tool 'primer' now. Do not answer until after the tool returns. "
        "Then summarize what the tool returned in 1-2 sentences."
    )

    result = await send_message_and_collect(
        ipc_client,
        message,
        "mcp_tool",
    )

    # Best-effort: drain any remaining events that arrived near the end of streaming.
    for _ in range(10):
        evt = await ipc_client.get_event(timeout=0.1)
        if not evt:
            break
        events.append(evt)

    # Reset callback to avoid cross-test bleed
    ipc_client.set_event_callback(None)

    # De-dupe events (callback + queue drain can capture the same event twice)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for e in events:
        key = (
            str(e.get("event_type", "")),
            str(e.get("timestamp", "")),
            str(e.get("content", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    events = deduped

    result["events"] = events
    result["mcp_tool_events"] = [
        e for e in events if str(e.get("event_type", "")).startswith("mcp_tool_")
    ]
    result["observed_tool_call"] = any(
        e.get("event_type") == "mcp_tool_call_started" for e in events
    )

    write_result("mcp_tool", result)

    # Log summary
    if result["success"]:
        logger.info(f"✓ MCP tool test completed in {result['duration_seconds']:.2f}s")
        response_lower = result["response"].lower()

        # Check if primer was actually used
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
async def test_multi_turn_conversation(ipc_client):
    """Test multi-turn conversation (state persistence)."""
    test_name = "multi_turn"
    results = []

    # Turn 1
    message1 = "My name is TestUser. Remember this."
    result1 = await send_message_and_collect(ipc_client, message1, f"{test_name}_turn1")
    results.append(result1)

    # Turn 2 - reference previous context
    message2 = "What is my name?"
    result2 = await send_message_and_collect(ipc_client, message2, f"{test_name}_turn2")
    results.append(result2)

    # Combine results
    combined = {
        "test_name": test_name,
        "timestamp": datetime.now().isoformat(),
        "turns": results,
        "success": all(r["success"] for r in results),
    }

    write_result("multi_turn", combined)

    # Log summary
    if combined["success"]:
        logger.info(f"✓ Multi-turn test completed ({len(results)} turns)")
    else:
        logger.error("✗ Multi-turn test failed")


if __name__ == "__main__":
    """Run tests directly for debugging."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    async def run_all_tests():
        """Run all integration tests."""
        client = IPCClient(host=IPC_HOST, port=IPC_PORT)

        try:
            connected = await client.connect()
            if not connected:
                logger.error("Failed to connect to Progenitor IPC server")
                logger.error(f"Make sure Progenitor is running on {IPC_HOST}:{IPC_PORT}")
                sys.exit(1)

            logger.info("Connected to Progenitor IPC server")
            logger.info("=" * 60)

            # Run tests
            await test_chat_basic(client)
            await asyncio.sleep(1)

            await test_chat_streaming(client)
            await asyncio.sleep(1)

            await test_schedule_task(client)
            await asyncio.sleep(1)

            await test_build_request(client)
            await asyncio.sleep(1)

            await test_system_tool(client)
            await asyncio.sleep(1)

            await test_mcp_tool(client)
            await asyncio.sleep(1)

            await test_multi_turn_conversation(client)

            logger.info("=" * 60)
            logger.info("All tests completed. Check results directory for output.")
            logger.info(f"Results: {RESULTS_DIR}")

        except Exception as e:
            logger.error(f"Test run failed: {e}", exc_info=True)
            sys.exit(1)

        finally:
            await client.disconnect()

    asyncio.run(run_all_tests())
