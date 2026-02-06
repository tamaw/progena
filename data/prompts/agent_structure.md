# Agent Structure Specification

## Directory Structure (CRITICAL - MUST FOLLOW EXACTLY)

**DO NOT create files directly in app/agents/. ALL agent files MUST go in a subdirectory.**

Create the agent in the subdirectory `app/agents/{agent_name}/` with these EXACT paths:
- `app/agents/{agent_name}/__init__.py` - Export the main agent class
- `app/agents/{agent_name}/agent.py` - Main agent implementation (REQUIRED)
- `app/agents/{agent_name}/README.md` - Documentation

WRONG: `app/agents/{agent_name}_agent.py` or `app/agents/{agent_name}.py`
CORRECT: `app/agents/{agent_name}/agent.py`

## HTTP Agent Requirements (CRITICAL)

All HTTP agents MUST implement these endpoints. Each agent is self-contained - there is no base class to inherit from.

### Required Endpoints

1. **GET `/.well-known/agent.json`** - Agent Card (A2A Standard)
   Returns JSON describing the agent:
   ```json
   {
       "name": "agent_name",
       "description": "What the agent does",
       "version": "0.1.0",
       "protocol": "a2a",
       "capabilities": ["capability_1", "capability_2"],
       "endpoint": "http://localhost:{port}"
   }
   ```

2. **GET `/health`** - Health Check
   Returns JSON with agent health status:
   ```json
   {
       "status": "healthy",
       "name": "agent_name",
       "version": "0.1.0",
       "uptime_seconds": 123.45,
       "timestamp": "2024-01-01T00:00:00Z"
   }
   ```

3. **POST `/`** - Main Request Handler
   Accepts JSON requests and returns responses:

   Request format:
   ```json
   {
       "request": "the main request content",
       "context": {},
       "id": "optional-request-id"
   }
   ```

   Response format:
   ```json
   {
       "success": true,
       "response": "the response content",
       "error": null,
       "metadata": {}
   }
   ```

### Status Protocol

Agents MUST output status lines to stdout for the agent manager:
```
STATUS:starting:Initializing server
STATUS:running:Server started on port {port}
STATUS:executing:Processing request
STATUS:waiting:Idle, waiting for requests
STATUS:failed:Error message
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

### Example HTTP Agent Structure

```python
#!/usr/bin/env python
"""Example HTTP Agent implementing A2A protocol."""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from typing import Any, Optional

from aiohttp import web


def emit_status(status: str, message: str) -> None:
    """Emit status for agent manager."""
    print(f"STATUS:{status}:{message}", flush=True)


@dataclass
class AgentCard:
    name: str
    description: str
    version: str = "0.1.0"
    protocol: str = "a2a"
    capabilities: list[str] = field(default_factory=list)
    endpoint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "protocol": self.protocol,
            "capabilities": self.capabilities,
            "endpoint": self.endpoint,
        }


class MyAgent:
    """Your agent implementation - fully self-contained, no base class."""

    AGENT_ID = "my_agent"
    AGENT_NAME = "My Agent"
    DESCRIPTION = "What this agent does"
    CAPABILITIES = ["capability_1", "capability_2"]

    def __init__(self, port: int):
        self.port = port
        self.startup_time = datetime.now(timezone.utc)

        async def lifespan(app: web.Application):
            emit_status("starting", f"Initializing {self.AGENT_NAME}")
            yield
            emit_status("stopping", "Shutting down")

        self.app = web.Application()
        self.app.cleanup_ctx.append(self._lifespan_wrapper(lifespan))
        self._setup_routes()

    def _lifespan_wrapper(self, lifespan):
        async def _ctx(app: web.Application):
            async with lifespan(app):
                yield
        return _ctx

    def _setup_routes(self) -> None:
        self.app.router.add_get("/.well-known/agent.json", self._handle_agent_card)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_post("/", self._handle_request)

    async def _handle_agent_card(self, request: web.Request) -> web.Response:
        card = AgentCard(
            name=self.AGENT_NAME,
            description=self.DESCRIPTION,
            capabilities=self.CAPABILITIES,
            endpoint=f"http://localhost:{self.port}",
        )
        return web.json_response(card.to_dict())

    async def _handle_health(self, request: web.Request) -> web.Response:
        uptime = (datetime.now(timezone.utc) - self.startup_time).total_seconds()
        return web.json_response({
            "status": "healthy",
            "name": self.AGENT_NAME,
            "version": "0.1.0",
            "uptime_seconds": uptime,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _handle_request(self, request: web.Request) -> web.Response:
        emit_status("executing", "Processing request")
        try:
            data = await request.json()
            # Your logic here
            result = await self.process(data.get("request", ""), data.get("context", {}))
            emit_status("waiting", "Idle")
            return web.json_response({"success": True, "response": result})
        except Exception as e:
            emit_status("waiting", f"Error: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def process(self, request: str, context: dict) -> str:
        """Your agent logic goes here."""
        return f"Processed: {request}"

    async def run(self) -> None:
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", self.port)
        await site.start()
        emit_status("running", f"Server started on port {self.port}")
        while True:
            await asyncio.sleep(3600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    agent = MyAgent(port=args.port)
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
```

## STDIO Agent Alternative

For simpler agents that don't need HTTP, use stdio transport:

```python
import sys
import json
import asyncio

class MyAgent:
    AGENT_ID = "my_agent"
    AGENT_NAME = "My Agent"
    DESCRIPTION = "What this agent does"
    CAPABILITIES = ["capability_1"]

    async def invoke(self, request: dict) -> dict:
        # Your logic here
        return {"success": True, "response": "Hello!"}

    def get_agent_card(self) -> dict:
        return {
            "name": self.AGENT_NAME,
            "description": self.DESCRIPTION,
            "capabilities": self.CAPABILITIES,
        }


def main():
    agent = MyAgent()
    input_data = json.loads(sys.stdin.read())
    result = asyncio.run(agent.invoke(input_data))
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
```

## Agent Class Requirements

Every agent (HTTP or stdio) MUST have these class attributes:
- `AGENT_ID` - Unique snake_case identifier
- `AGENT_NAME` - Human-readable name
- `DESCRIPTION` - What the agent does
- `CAPABILITIES` - List of capability strings for discovery

## Testing Requirements

Create tests in `app/agents/{agent_name}/tests/`:

### tests/test_{agent_name}.py

```python
"""Tests for {agent_name} agent."""

import pytest
import aiohttp
from aiohttp import web


@pytest.mark.asyncio
async def test_health_endpoint(agent_url: str):
    """Test agent health endpoint."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{agent_url}/health") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_agent_card(agent_url: str):
    """Test agent card endpoint."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{agent_url}/.well-known/agent.json") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "name" in data
            assert "capabilities" in data


@pytest.mark.asyncio
async def test_invoke_basic(agent_url: str):
    """Test basic agent invocation."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{agent_url}/",
            json={"request": "Hello!", "context": {}},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["success"] is True
            assert "response" in data


@pytest.fixture
def agent_url():
    """Agent URL for testing - configure via environment or use default."""
    import os
    return os.getenv("AGENT_URL", "http://localhost:8080")
```

## Integration with FastMCP Tools

Agents can use FastMCP tools from `app/tools/` by:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def call_tool(tool_name: str, args: dict) -> str:
    """Call a FastMCP tool."""
    params = StdioServerParameters(
        command="python",
        args=["-m", f"app.tools.{tool_name}.server"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=args)
            return result.content[0].text if result.content else ""
```

## Registration

After building, run `rediscover_agents` system tool to register in `registry.json`.

