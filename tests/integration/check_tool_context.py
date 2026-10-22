"""Check what tool context is being provided to the LLM."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.registry.store import ToolRegistry


async def check_tool_context():
    """Check what tools are in the context."""
    registry_path = PROJECT_ROOT / "data" / "config" / "agents.json"
    registry = ToolRegistry(registry_path=registry_path)
    await registry.load()

    context = registry.get_tool_context()
    print("=== Tool Context ===\n")
    print(context)
    print("\n=== End Context ===")

    # Check if primer is mentioned
    if "primer" in context.lower():
        print("\n✓ 'primer' IS in the tool context")
    else:
        print("\n✗ 'primer' NOT in the tool context")


if __name__ == "__main__":
    asyncio.run(check_tool_context())
