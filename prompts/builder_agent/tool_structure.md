# FastMCP Tool Specification: {tool_name}

> This is the canonical template for building FastMCP tools that live in `app/mcp/`.
> These tools are self-hosted and can be used by A2A agents and the Progenitor system.

## Directory Structure

Create the tool in `app/mcp/{tool_name}/`:

```
app/mcp/{tool_name}/
├── __init__.py           # Package init with exports
├── server.py             # FastMCP server implementation
├── spec.md               # This specification (copy of this file)
└── tests/
    ├── __init__.py
    └── test_{tool_name}.py   # Pytest tests for the tool
```

## Implementation Requirements

### server.py - FastMCP Server

```python
"""
{tool_class} - FastMCP tool server.

Provides MCP tools for [describe what the tool does].
"""

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("{tool_name}")


@mcp.tool()
async def {primary_function}(param1: str, param2: int = 10) -> str:
    """
    [Description of what this tool does].

    Args:
        param1: [Description of param1]
        param2: [Description of param2, with default]

    Returns:
        [Description of return value]
    """
    # Implementation here
    result = f"Processed {param1} with {param2}"
    return result


# Add more @mcp.tool() decorated functions as needed


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
```

### __init__.py - Package Exports

```python
"""
{tool_class} - FastMCP tool for [brief description].
"""

from .server import mcp, {primary_function}

__all__ = ["mcp", "{primary_function}"]
```

### tests/test_{tool_name}.py - Pytest Tests

```python
"""
Tests for {tool_name} FastMCP tool.
"""

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def server_params():
    """Create server parameters for testing."""
    return StdioServerParameters(
        command="python",
        args=["app/mcp/{tool_name}/server.py"],
    )


@pytest.mark.asyncio
async def test_{primary_function}_basic(server_params):
    """Test basic {primary_function} functionality."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            assert "{primary_function}" in tool_names

            # Call the tool
            result = await session.call_tool(
                "{primary_function}",
                arguments={{"param1": "test", "param2": 5}}
            )

            assert result.content
            assert len(result.content) > 0
            # Add specific assertions based on expected output


@pytest.mark.asyncio
async def test_{primary_function}_edge_cases(server_params):
    """Test edge cases and error handling."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Test with edge case inputs
            result = await session.call_tool(
                "{primary_function}",
                arguments={{"param1": "", "param2": 0}}
            )

            # Verify graceful handling
            assert result.content is not None
```

## Behavioral Requirements

1. **Error Handling**: All tools must handle errors gracefully and return meaningful error messages
2. **Type Safety**: Use proper type hints for all function parameters and return values
3. **Documentation**: Every tool function must have a comprehensive docstring
4. **Async**: Prefer async implementations for I/O operations
5. **Logging**: Use Python logging module for debugging (not print statements)

## Testing Checklist

Before marking as complete, verify:

- [ ] `python app/mcp/{tool_name}/server.py` starts without errors
- [ ] `pytest app/mcp/{tool_name}/tests/ -v` passes all tests
- [ ] Tool can be called via MCP client
- [ ] Error cases return proper error responses (not exceptions)
- [ ] Docstrings are complete and accurate

## Registration

After building, the tool should be discoverable by:
1. Adding to the tool registry via `rediscover_tools` system tool
2. Or manually registering in `data/config/mcp.json`

## Example Tool Entry (mcp.json)

```json
{
  "name": "{tool_name}",
  "description": "[What this tool does]",
  "transport": "stdio",
  "command": "python",
    "args": ["app/mcp/{tool_name}/server.py"],
  "status": "available"
}
```
