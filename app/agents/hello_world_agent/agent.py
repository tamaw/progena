"""Hello World Agent implementation."""

import asyncio
import json
import sys
import time
from datetime import UTC, datetime

from aiohttp import web


class HelloWorldAgent:
    """A minimal agent that responds with 'Hello World!' to any request."""

    AGENT_ID = "hello_world_agent"
    AGENT_NAME = "Hello World Agent"
    DESCRIPTION = "Responds with 'Hello World!' to any request."
    CAPABILITIES = ["hello_world", "static_response"]
    VERSION = "0.1.0"

    def __init__(self, port: int = 8000):
        """Initialize the Hello World Agent."""
        self.port = port
        self.app = web.Application()
        self.start_time = time.time()
        self._setup_routes()

    def _setup_routes(self):
        """Set up HTTP routes."""
        self.app.router.add_get("/.well-known/agent.json", self._handle_agent_card)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_post("/", self._handle_request)

    async def _handle_agent_card(self, _request: web.Request) -> web.Response:
        """Handle agent card endpoint."""
        card = {
            "name": self.AGENT_NAME,
            "description": self.DESCRIPTION,
            "version": self.VERSION,
            "protocol": "a2a",
            "capabilities": self.CAPABILITIES,
            "endpoint": f"http://localhost:{self.port}",
        }
        return web.json_response(card)

    async def _handle_health(self, _request: web.Request) -> web.Response:
        """Handle health check endpoint."""
        uptime = time.time() - self.start_time
        health = {
            "status": "healthy",
            "name": self.AGENT_NAME,
            "version": self.VERSION,
            "uptime_seconds": uptime,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return web.json_response(health)

    async def _handle_request(self, request: web.Request) -> web.Response:
        """Handle main request endpoint."""
        self._emit_status("executing", "Processing request")

        try:
            # Parse request body (handle errors gracefully)
            try:
                if request.content_length:
                    body = await request.json()
                else:
                    body = {}
            except (json.JSONDecodeError, ValueError):
                body = {}

            # Process the request (ignore all input)
            response_text = await self.process(
                body.get("request", ""),
                body.get("context", {}),
            )

            # Return success response
            response = {
                "success": True,
                "response": response_text,
                "error": None,
                "metadata": {},
            }

            self._emit_status("waiting", "Idle, waiting for requests")
            return web.json_response(response)

        except Exception as e:
            self._emit_status("failed", str(e))
            return web.json_response(
                {
                    "success": False,
                    "response": None,
                    "error": str(e),
                    "metadata": {},
                },
                status=500,
            )

    async def process(self, _request: str = "", context: dict | None = None) -> str:
        """Process a request and return response.

        Always returns 'Hello World!' regardless of input.
        """
        if context is None:
            context = {}
        return "Hello World!"

    def _emit_status(self, state: str, message: str):
        """Emit a status line to STDOUT."""
        status_line = f"STATUS:{state}:{message}"
        print(status_line, file=sys.stdout, flush=True)

    async def start(self):
        """Start the agent server."""
        self._emit_status("starting", "Initializing Hello World Agent")

        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()

        self._emit_status("running", f"Server started on port {self.port}")

        # Keep the server running
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            self._emit_status("stopping", "Shutting down")
        finally:
            await runner.cleanup()
            self._emit_status("stopped", "Agent terminated")


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Hello World Agent")
    parser.add_argument("--port", type=int, default=8000, help="Port to run on")
    args = parser.parse_args()

    agent = HelloWorldAgent(port=args.port)
    await agent.start()


if __name__ == "__main__":
    asyncio.run(main())
