#!/usr/bin/env python3
"""
Demo script: Invoke Builder from Progenitor.

Shows how to use the A2A protocol to:
1. Invoke Builder asynchronously
2. Get immediate task_id back
3. Monitor task progress

Run this AFTER starting both:
  - Builder:  python -m uvicorn app.agents.builder.agent:app --port 8108
  - (Progenitor will be the caller)
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx

# Add workspace root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def invoke_builder_via_tool() -> tuple[str, dict]:
    """
    Use the generic InvokeAgentTool, forcing selection of builder.
    
    In production Progenitor uses the LLM routing path automatically.
    The demo forces agent='builder' to skip the LLM call.
    """
    from app.progenitor.tools.invoke_agent import InvokeAgentTool
    from app.progenitor.tools.protocol import ToolContext
    
    tool = InvokeAgentTool()
    
    context = ToolContext(
        user_input="Create a comprehensive README.md for Builder that documents: purpose, capabilities, available skills (plan, build, test, lint, pr, python-search), configuration, how to invoke via A2A protocol, usage examples, and architecture details.",
        # Force builder so the demo works without a live LLM routing call.
        # In normal Progenitor usage, omit 'agent' and let LLM routing pick.
        tool_args={"agent": "builder", "timeout": 30},
        tool_registry=MagicMock(),
        llm_client=MagicMock(),
    )
    
    logger.info("[*] Using InvokeAgentTool (agent=builder, forced)...")
    logger.info("    User input: %s...", context.user_input[:60])
    print()
    
    try:
        result = await tool.execute(context)
        
        if result.success:
            return result.data.get("task_id", "unknown"), result.data
        else:
            return "", {"error": result.error}
            
    except Exception as e:
        logger.exception("Tool execution failed")
        return "", {"error": str(e)}


async def check_agent_health() -> bool:
    """Check if Builder is running."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://localhost:8108/health")
            if resp.status_code == 200:
                health = resp.json()
                logger.info("[OK] Builder is running")
                logger.info("     Status: %s", health.get("status"))
                logger.info("     Version: %s", health.get("version"))
                logger.info("     Uptime: %.1fs", health.get("uptime_seconds", 0))
                return True
    except Exception as e:
        logger.error("[ERROR] Cannot reach Builder: %s", e)
    return False


async def main():
    """Main demo flow."""
    print("\n" + "=" * 70)
    print("  DEMO: Invoke Builder from Progenitor (A2A Protocol)")
    print("=" * 70)
    print()
    
    # 1. Check health
    print("[STEP 1] Checking if Builder is running...")
    print("-" * 70)
    if not await check_agent_health():
        print("\nERROR: Builder is not running.")
        print("Start it first, then run this script again:")
        print("  $ python -m uvicorn app.agents.builder.agent:app --port 8108")
        return 1
    print()
    
    # 2. Invoke Builder using the actual system tool
    print("[STEP 2] Invoking Builder asynchronously...")
    print("(Using the actual InvokeAgentTool from Progenitor)")
    print("-" * 70)
    task_id, result = await invoke_builder_via_tool()
    
    if not task_id:
        logger.error("Invocation failed: %s", result)
        return 1
    
    logger.info("[OK] Task accepted!")
    logger.info("     Task ID: %s", task_id)
    print()
    
    # 3. Show response
    print("[STEP 3] A2A Response:")
    print("-" * 70)
    print(json.dumps(result, indent=2, default=str))
    print()
    
    # 4. Summary
    print("[SUMMARY]")
    print("-" * 70)
    print(f"Task ID         : {task_id}")
    print(f"Status          : ACCEPTED (processing asynchronously)")
    print(f"Expected output : Pull request on GitHub")
    print()
    print("Next steps:")
    print("  1. Check Builder logs for progress")
    print("  2. Monitor at: https://github.com/tamaw/progenitor/pulls")
    print("  3. This is how Progenitor invokes Builder internally")
    print()
    print("=" * 70)
    print()
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
