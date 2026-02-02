#!/usr/bin/env python
"""Weather Lookup HTTP Agent

Provides current weather information using OpenWeather API.

Endpoints:
- GET /.well-known/agent.json
- GET /health
- POST / (main request handler)

Environment:
- OPENWEATHER_API_KEY: OpenWeather API key (required for weather lookups)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from aiohttp import web, ClientSession


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


class WeatherLookupAgent:
    """HTTP agent that returns current weather data for a location using OpenWeather API.

    Class attributes required by the agent registry/discovery:
    - AGENT_ID, AGENT_NAME, DESCRIPTION, CAPABILITIES

    The agent exposes an async invoke method and get_agent_card for compatibility.
    """

    AGENT_ID = "weather_lookup"
    AGENT_NAME = "Weather Lookup"
    DESCRIPTION = "Retrieves current weather information from OpenWeather API for a given location and returns normalized, human-readable weather data."
    CAPABILITIES = ["weather_lookup", "current_weather", "location_based_query"]

    def __init__(self, port: int = 8080):
        self.port = port
        self.app = web.Application()
        self.startup_time = datetime.now(timezone.utc)
        self._setup_routes()

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
            req_text = data.get("request") or ""
            context = data.get("context") or {}
            result = await self.process(req_text, context)
            emit_status("waiting", "Idle")
            return web.json_response({"success": True, "response": result, "error": None, "metadata": {}})
        except web.HTTPException:
            raise
        except Exception as e:  # pragma: no cover - runtime error reporting
            emit_status("failed", f"Error: {e}")
            return web.json_response({"success": False, "response": None, "error": str(e), "metadata": {}}, status=500)

    async def process(self, request: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Process a request and return normalized weather information.

        request: expected to be a location string (e.g., "London,UK" or "Seattle")
        context: optional dict with keys like "location"
        """
        # Determine location
        location = request.strip() if request else context.get("location") or context.get("query") or ""
        if not location:
            raise ValueError("No location provided in request or context")

        api_key = os.environ.get("OPENWEATHER_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENWEATHER_API_KEY environment variable is not set")

        # Fetch from OpenWeather
        async with ClientSession() as session:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {"q": location, "appid": api_key, "units": "metric"}
            async with session.get(url, params=params, timeout=20) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"OpenWeather API error: {resp.status} - {text}")
                payload = await resp.json()

        # Normalize payload
        coord = payload.get("coord", {})
        sys = payload.get("sys", {})
        main = payload.get("main", {})
        wind = payload.get("wind", {})
        weather_list = payload.get("weather") or []
        weather = weather_list[0] if weather_list else {}

        def _iso(ts: Optional[int]) -> Optional[str]:
            if ts is None:
                return None
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        normalized = {
            "location_name": f"{payload.get('name')}, {sys.get('country') if sys.get('country') else ''}".strip(', '),
            "coordinates": {"lat": coord.get("lat"), "lon": coord.get("lon")},
            "temperature_celsius": main.get("temp"),
            "feels_like_celsius": main.get("feels_like"),
            "pressure_hpa": main.get("pressure"),
            "humidity_percent": main.get("humidity"),
            "weather_main": weather.get("main"),
            "weather_description": weather.get("description"),
            "wind_speed_m_s": wind.get("speed"),
            "sunrise": _iso(sys.get("sunrise")),
            "sunset": _iso(sys.get("sunset")),
            "observation_time": _iso(payload.get("dt")),
            "raw": payload,
        }
        return normalized

    async def invoke(self, input: str) -> str:  # required API compatibility
        """Simple invoke compatibility method.

        This method accepts a string (location) and returns a stringified JSON result.
        """
        result = await self.process(input, {})
        return json.dumps(result, default=str)

    def get_agent_card(self) -> dict:
        return {
            "name": self.AGENT_NAME,
            "description": self.DESCRIPTION,
            "version": "0.1.0",
            "protocol": "a2a",
            "capabilities": self.CAPABILITIES,
            "endpoint": f"http://localhost:{self.port}",
        }

    async def run(self) -> None:
        emit_status("starting", f"Initializing {self.AGENT_NAME}")
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", self.port)
        await site.start()
        emit_status("running", f"Server started on port {self.port}")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            emit_status("stopping", "Shutting down")
            await runner.cleanup()
            emit_status("stopped", "Agent terminated")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    agent = WeatherLookupAgent(port=args.port)
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
