import os
import re
import json
import time
import asyncio
from datetime import datetime, timezone
import aiohttp
from aiohttp import web


class WeatherAgent:
    AGENT_ID = "weather_agent"
    AGENT_NAME = "Weather Agent"
    DESCRIPTION = "Retrieves current weather conditions and weather forecasts for a specified location."
    CAPABILITIES = [
        "current_weather",
        "weather_forecast",
        "location_based_weather",
    ]

    def __init__(self):
        self._start_time = time.time()
        self._api_key = os.environ.get("WEATHER_API_KEY")
        self._app = web.Application()
        self._app.add_routes([
            web.get("/.well-known/agent.json", self.agent_card_handler),
            web.get("/health", self.health_handler),
            web.post("/", self.main_handler),
        ])

    async def agent_card_handler(self, request: web.Request) -> web.Response:
        port = request.app.get("port", 0) or request.transport.get_extra_info("peername")
        card = {
            "name": self.AGENT_NAME,
            "description": self.DESCRIPTION,
            "version": "0.1.0",
            "protocol": "a2a",
            "capabilities": self.CAPABILITIES,
            "endpoint": f"http://localhost:{port}",
        }
        return web.json_response(card)

    async def health_handler(self, request: web.Request) -> web.Response:
        uptime = time.time() - self._start_time
        payload = {
            "status": "healthy",
            "name": self.AGENT_NAME,
            "version": "0.1.0",
            "uptime_seconds": round(uptime, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return web.json_response(payload)

    async def main_handler(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({
                "success": False,
                "response": None,
                "error": "Invalid JSON request.",
                "metadata": {},
            })

        req_text = data.get("request")
        context = data.get("context", {}) or {}
        req_id = data.get("id")

        if not req_text or not isinstance(req_text, str):
            return web.json_response({
                "success": False,
                "response": None,
                "error": "No location specified. Please provide a city or location.",
                "metadata": {},
            })

        print("STATUS:executing:Processing weather request")
        try:
            report = await self.process(req_text, context)
            # If process returns a dict with error
            if isinstance(report, dict) and not report.get("success", True):
                print(f"STATUS:failed:{report.get('error')}")
                return web.json_response(report)

            # Successful string report
            metadata = {
                "location": getattr(self, "_last_location_str", ""),
                "type": getattr(self, "_last_type", "current"),
                "source": "openweathermap",
                "units": "metric",
            }
            response = {
                "success": True,
                "response": report,
                "error": None,
                "metadata": metadata,
            }
            print("STATUS:waiting:Idle, waiting for requests")
            return web.json_response(response)
        except Exception as e:
            print(f"STATUS:failed:{str(e)}")
            return web.json_response({
                "success": False,
                "response": None,
                "error": "Unable to retrieve weather data for the specified location.",
                "metadata": {},
            })

    async def process(self, request: str, context: dict) -> str:
        # Determine intent
        text = request.lower()
        is_forecast = any(k in text for k in ["tomorrow", "forecast", "day", "week", "weekly"]) and not any(
            w in text for w in ["right now", "now", "currently"]
        )

        # Try to extract location from text
        loc = None
        m = re.search(r"(?:in|for)\s+([A-Za-z\s,]+)", request, re.IGNORECASE)
        if m:
            loc = m.group(1).strip()
        if not loc and isinstance(context, dict):
            loc = context.get("location")
        if not loc:
            # No location determined
            return {
                "success": False,
                "response": None,
                "error": "No location specified. Please provide a city or location.",
                "metadata": {},
            }

        # Geocode the location to lat/lon
        if not self._api_key:
            return {
                "success": False,
                "response": None,
                "error": "Unable to retrieve weather data for the specified location.",
                "metadata": {"location": loc},
            }

        geocoded = await self._geocode(loc)
        if not geocoded:
            return {
                "success": False,
                "response": None,
                "error": "Unable to retrieve weather data for the specified location.",
                "metadata": {"location": loc},
            }

        lat = geocoded.get("lat")
        lon = geocoded.get("lon")
        loc_name = geocoded.get("name")
        country = geocoded.get("country")
        location_str = f"{loc_name}, {country}" if country else loc_name
        self._last_location_str = location_str

        if is_forecast:
            self._last_type = "forecast"
            forecast = await self._get_forecast(lat, lon)
            if not forecast:
                return {
                    "success": False,
                    "response": None,
                    "error": "Unable to retrieve weather data for the specified location.",
                    "metadata": {"location": loc},
                }
            # Format forecast exactly as specified
            # Use the next two days: tomorrow and day after tomorrow
            lines = [f"Weather forecast for {location_str}:", ""]
            # daily[1] is tomorrow, daily[2] is day after
            days = forecast.get("daily", [])
            def fmt_day(day_idx, label):
                try:
                    d = days[day_idx]
                except Exception:
                    return None
                high = round(d.get("temp", {}).get("max"))
                low = round(d.get("temp", {}).get("min"))
                condition = d.get("weather", [{}])[0].get("description", "")
                return f"{label}:\n  High: {high}°C\n  Low: {low}°C\n  Condition: {condition}\n"

            tomorrow = fmt_day(1, "Tomorrow")
            if tomorrow:
                lines.append(tomorrow)
            day_after = fmt_day(2, "Day after tomorrow")
            if day_after:
                lines.append(day_after)

            final = "\n".join(lines).strip() + "\n"
            return final
        else:
            self._last_type = "current"
            current = await self._get_current(lat, lon)
            if not current:
                return {
                    "success": False,
                    "response": None,
                    "error": "Unable to retrieve weather data for the specified location.",
                    "metadata": {"location": loc},
                }
            # Normalize and format
            temp = round(current.get("main", {}).get("temp"))
            condition = current.get("weather", [{}])[0].get("description", "")
            humidity = current.get("main", {}).get("humidity")
            wind_ms = current.get("wind", {}).get("speed", 0)
            wind_kmh = round(wind_ms * 3.6)

            report = (
                f"Current weather in {location_str}:\n\n"
                f"Temperature: {temp}°C\n"
                f"Condition: {condition}\n"
                f"Humidity: {humidity}%\n"
                f"Wind: {wind_kmh} km/h\n"
            )
            return report

    async def _geocode(self, location: str) -> dict:
        url = "http://api.openweathermap.org/geo/1.0/direct"
        params = {"q": location, "limit": 1, "appid": self._api_key}
        async with aiohttp.ClientSession() as sess:
            try:
                async with sess.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    if not data:
                        return None
                    item = data[0]
                    return {
                        "lat": item.get("lat"),
                        "lon": item.get("lon"),
                        "name": item.get("name"),
                        "country": item.get("country"),
                    }
            except Exception:
                return None

    async def _get_current(self, lat: float, lon: float) -> dict:
        url = "http://api.openweathermap.org/data/2.5/weather"
        params = {"lat": lat, "lon": lon, "units": "metric", "appid": self._api_key}
        async with aiohttp.ClientSession() as sess:
            try:
                async with sess.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.json()
            except Exception:
                return None

    async def _get_forecast(self, lat: float, lon: float) -> dict:
        url = "https://api.openweathermap.org/data/2.5/onecall"
        params = {"lat": lat, "lon": lon, "exclude": "minutely,hourly,alerts", "units": "metric", "appid": self._api_key}
        async with aiohttp.ClientSession() as sess:
            try:
                async with sess.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.json()
            except Exception:
                return None

    def run(self, port: int = 8080):
        # Expose port in app for agent card
        self._app["port"] = port
        print("STATUS:starting:Initializing Weather Agent")
        try:
            print(f"STATUS:running:Server started on port {port}")
            web.run_app(self._app, port=port)
        except Exception as e:
            print(f"STATUS:failed:{str(e)}")
        finally:
            print("STATUS:stopping:Shutting down")
            print("STATUS:stopped:Agent terminated")


# Allow running directly
if __name__ == "__main__":
    agent = WeatherAgent()
    port = int(os.environ.get("PORT", "8080"))
    agent.run(port)
