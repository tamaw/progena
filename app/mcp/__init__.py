"""
FastMCP Tools Package.

This directory contains self-hosted FastMCP tools that can be used by:
- A2A agents in app/agents/
- The Progenitor system itself
- External MCP clients

Each tool lives in its own subdirectory with:
- Dockerfile: container image used by the MCP deployment workflow
- server.py: FastMCP server implementation
- __init__.py: Package exports
- spec.md: Tool specification
- tests/: Pytest tests

Tools use the Model Context Protocol (MCP) with FastMCP decorators.

Example tool structure:
    app/mcp/my_tool/
    ├── Dockerfile
    ├── __init__.py
    ├── server.py  # mcp = FastMCP("my_tool"); @mcp.tool()
    ├── spec.md
    └── tests/
        └── test_my_tool.py

To run a tool:
    python app/mcp/my_tool/server.py

To test a tool:
    pytest app/mcp/my_tool/tests/ -v
"""
