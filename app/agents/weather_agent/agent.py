import os
import re
import time
import asyncio
from typing import Optional, Tuple, Dict, Any

import aiohttp
from aiohttp import web


class WeatherAgent:
    AGENT_ID = "weather_agent"
    AGENT_NAME = "Weather Agent"
    DESCRIPTION = (
        "Provides real-time weather conditions and short-term forecasts "
        "with clear, user-friendly explanations."
    )
    CAPABILITIES = [
        "current_weather",
        "short_term_forecast",
        "weather_explanation",
        "location_based_weather",
    ]

    def __init__(self, api_key: Optional[str] = None, session: Optional[aiohttp.ClientSession] = None):
        self.api_key = api_key or os.getenv("WEATHER_API_KEY")
        self.session = session

    async def _ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def process(self, request: str, context: dict) -> str:
        """
        Parse the request and context, call the weather API, and return a user-facing string.
        """
        # Basic intent check
        if not self._is_weather_request(request):
            raise UnsupportedRequestError("I can help with weather information only. Please ask about current conditions or forecasts.")

        # Determine units and language
        units = (context or {}).get("units", "metric")
        if units not in ("metric", "imperial"):
            units = "metric"
        language = (context or {}).get("language", "en")

        # Determine location
        location = (context or {}).get("location")
        if not location:
            location = self._extract_location_from_text(request)
        if not location:
            raise MissingLocationError("Please specify a location (for example: city or coordinates).")

        await self._ensure_session()

        # Call external API
        try:
            current, forecast = await self._fetch_weather(location, units=units, lang=language)
        except ExternalAPIError as e:
            raise

        # Build response based on whether user asked for forecast or current
        timeframe = self._detect_timeframe(request)

        if timeframe == "forecast":
            text = self._format_forecast(location, forecast, units)
        else:
            text = self._format_current(location, current, units)

        return text

    def _is_weather_request(self, text: str) -> bool:
        keywords = [
            "weather",
            "rain",
            "snow",
            "forecast",
            "temperature",
            "wind",
            "feels like",
            "humidity",
            "sunny",
            "cloud",
            "storm",
            "drizzle",
            "will it",
            "chance of",
        ]
        t = text.lower()
        return any(k in t for k in keywords)

    def _detect_timeframe(self, text: str) -> str:
        t = text.lower()
        if "forecast" in t or "tomorrow" in t or "day" in t or "3-day" in t or "3 day" in t:
            return "forecast"
        return "current"

    def _extract_location_from_text(self, text: str) -> Optional[str]:
        # look for "in <Location>" pattern
        m = re.search(r" in ([A-Za-z\s,.'-]+?)(?:\?|$| for | tomorrow| today| right now)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # look for coordinates like "48.85,2.35"
        m2 = re.search(r"(-?\d{1,3}\.\d+),\s*(-?\d{1,3}\.\d+)", text)
        if m2:
            return f"{m2.group(1)},{m2.group(2)}"
        return None

    async def _fetch_weather(self, location: str, units: str = "metric", lang: str = "en") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Uses OpenWeatherMap API (current + 5 day forecast). Expects WEATHER_API_KEY in env if not provided.
        Returns (current_data, forecast_data)
        """
        if not self.api_key:
            raise ExternalAPIError("Missing WEATHER_API_KEY environment variable")

        # Resolve location: if coordinates provided, use them directly, otherwise geocode via OWM
        lat = lon = None
        if re.match(r"^-?\d{1,3}\.\d+,-?\d{1,3}\.\d+$", location.strip()):
            lat, lon = [s.strip() for s in location.split(",")]
        else:
            # call OWM geocoding API
            geocode_url = "http://api.openweathermap.org/geo/1.0/direct"
            params = {"q": location, "limit": 1, "appid": self.api_key}
            async with self.session.get(geocode_url, params=params, raise_for_status=False) as resp:
                if resp.status != 200:
                    raise ExternalAPIError("Geocoding failed")
                data = await resp.json()
                if not data:
                    raise ExternalAPIError("Location not found by geocoding")
                lat = data[0].get("lat")
                lon = data[0].get("lon")
                # normalize location name
                display_name = ", ".join(filter(None, [data[0].get("name"), data[0].get("country")]))
                location = display_name

        # current weather
        curr_url = "https://api.openweathermap.org/data/2.5/weather"
        params_curr = {"lat": lat, "lon": lon, "appid": self.api_key, "units": units, "lang": lang}
        async with self.session.get(curr_url, params=params_curr, raise_for_status=False) as resp:
            if resp.status != 200:
                raise ExternalAPIError("Current weather API failed")
            current = await resp.json()

        # forecast (5 day / 3 hour)
        forecast_url = "https://api.openweathermap.org/data/2.5/forecast"
        params_f = {"lat": lat, "lon": lon, "appid": self.api_key, "units": units, "lang": lang}
        async with self.session.get(forecast_url, params=params_f, raise_for_status=False) as resp:
            if resp.status != 200:
                raise ExternalAPIError("Forecast API failed")
            forecast = await resp.json()

        # attach normalized location name if available
        if current.get("name"):
            location = f"{current.get('name')}, {current.get('sys', {}).get('country', '')}".strip(', ')

        return current, forecast

    def _format_current(self, location: str, current: Dict[str, Any], units: str) -> str:
        try:
            temp = current["main"]["temp"]
            feels = current["main"].get("feels_like")
            condition = current["weather"][0]["description"].capitalize()
            wind_speed = current.get("wind", {}).get("speed")
            humidity = current["main"].get("humidity")
        except Exception:
            raise ExternalAPIError("Unexpected current weather response format")

        # units display
        temp_unit = "°C" if units == "metric" else "°F"
        wind_unit = "km/h" if units == "metric" else "mph"
        # convert wind m/s to km/h if metric
        if units == "metric":
            if wind_speed is not None:
                wind_speed = round(float(wind_speed) * 3.6)
        else:
            if wind_speed is not None:
                wind_speed = round(float(wind_speed))

        # Build friendly sentence
        parts = []
        parts.append(f"Right now in {location}, it\u2019s {round(temp)}{temp_unit} and {condition}.")
        if wind_speed is not None:
            parts.append(f"There\u2019s a wind at about {wind_speed} {wind_unit}.")
        if feels is not None:
            parts.append(f"It feels like {round(feels)}{temp_unit}.")
        if humidity is not None:
            parts.append(f"Humidity is around {humidity}%.")

        return " ".join(parts)

    def _format_forecast(self, location: str, forecast: Dict[str, Any], units: str) -> str:
        # Build up to 3-day forecast from 3-hourly data
        try:
            items = forecast.get("list", [])
        except Exception:
            raise ExternalAPIError("Unexpected forecast response format")

        if not items:
            raise ExternalAPIError("No forecast data returned")

        # group by date
        days = {}
        for entry in items:
            dt_txt = entry.get("dt_txt")  # '2026-02-04 12:00:00'
            if not dt_txt:
                continue
            day = dt_txt.split(" ")[0]
            temp = entry.get("main", {}).get("temp")
            cond = entry.get("weather", [{}])[0].get("description", "")
            if day not in days:
                days[day] = {"temps": [], "conds": []}
            if temp is not None:
                days[day]["temps"].append(temp)
            if cond:
                days[day]["conds"].append(cond)

        # pick up to 3 days starting from tomorrow
        sorted_days = sorted(days.keys())
        from datetime import datetime, timedelta
        today = datetime.utcnow().date().isoformat()
        upcoming = [d for d in sorted_days if d >= today]
        # include up to 3 days
        upcoming = upcoming[:3]

        temp_unit = "°C" if units == "metric" else "°F"

        paragraphs = []
        for d in upcoming:
            temps = days[d]["temps"]
            conds = days[d]["conds"]
            if temps:
                lo = round(min(temps))
                hi = round(max(temps))
                main_cond = max(set(conds), key=conds.count) if conds else "" 
                # make date human-friendly
                dt_obj = datetime.fromisoformat(d)
                label = dt_obj.strftime("%A")
                paragraphs.append(f"{label} in {location} will be between {lo}{temp_unit} and {hi}{temp_unit} with {main_cond}.")

        if not paragraphs:
            raise ExternalAPIError("Insufficient forecast data")

        return "\n\n".join(paragraphs)


# Custom exceptions
class ExternalAPIError(Exception):
    pass


class MissingLocationError(Exception):
    pass


class UnsupportedRequestError(Exception):
    pass


# HTTP server (A2A-compliant)
async def create_app(agent: WeatherAgent, host: str = "0.0.0.0", port: int = 8080) -> web.Application:
    app = web.Application()
    start_time = time.time()

    async def agent_card(request_http):
        card = {
            "name": agent.AGENT_NAME,
            "description": agent.DESCRIPTION,
            "version": "0.1.0",
            "protocol": "a2a",
            "capabilities": agent.CAPABILITIES,
            "endpoint": f"http://{host}:{port}",
        }
        return web.json_response(card)

    async def health(request_http):
        uptime = time.time() - start_time
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload = {
            "status": "healthy",
            "name": agent.AGENT_NAME,
            "version": "0.1.0",
            "uptime_seconds": round(uptime, 2),
            "timestamp": now,
        }
        return web.json_response(payload)

    async def handle(request_http):
        print("STATUS:executing:Processing request")
        try:
            body = await request_http.json()
        except Exception:
            print("STATUS:failed:Invalid JSON in request")
            print("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({
                "success": False,
                "response": None,
                "error": "Invalid JSON request",
                "metadata": {},
            }, status=400)

        req_text = body.get("request")
        context = body.get("context", {}) or {}
        req_id = body.get("id")

        if not req_text:
            print("STATUS:failed:Missing request field")
            print("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({
                "success": False,
                "response": None,
                "error": "Missing 'request' field",
                "metadata": {},
            }, status=400)

        # units default
        units = context.get("units", "metric")
        if units not in ("metric", "imperial"):
            units = "metric"

        # early check for weather intent
        try:
            response_text = await agent.process(req_text, context)
        except MissingLocationError as e:
            print("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({
                "success": False,
                "response": None,
                "error": str(e),
                "metadata": {},
            }, status=400)
        except UnsupportedRequestError as e:
            print("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({
                "success": False,
                "response": None,
                "error": str(e),
                "metadata": {},
            }, status=400)
        except ExternalAPIError as e:
            print(f"STATUS:failed:{str(e)}")
            print("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({
                "success": False,
                "response": None,
                "error": "I couldn\'t retrieve weather data right now. Please try again later.",
                "metadata": {"data_source": "external_weather_api"},
            }, status=502)
        except Exception as e:
            print(f"STATUS:failed:{str(e)}")
            print("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({
                "success": False,
                "response": None,
                "error": "Internal error",
                "metadata": {},
            }, status=500)

        # success
        metadata = {
            "location": context.get("location") or "(resolved via query)",
            "units": units,
            "data_source": "external_weather_api",
        }

        print("STATUS:waiting:Idle, waiting for requests")
        return web.json_response({
            "success": True,
            "response": response_text,
            "error": None,
            "metadata": metadata,
        })

    app.router.add_get("/.well-known/agent.json", agent_card)
    app.router.add_get("/health", health)
    app.router.add_post("/", handle)

    return app


def main():
    port = int(os.getenv("AGENT_PORT", "8080"))
    host = os.getenv("AGENT_HOST", "127.0.0.1")

    print("STATUS:starting:Initializing Weather Agent")
    agent = WeatherAgent()

    loop = asyncio.get_event_loop()
    app = loop.run_until_complete(create_app(agent, host=host, port=port))

    runner = web.AppRunner(app)

    async def _run():
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        print(f"STATUS:running:Server started on port {port}")
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    try:
        loop.run_until_complete(_run())
    except KeyboardInterrupt:
        print("STATUS:stopping:Shutting down")
    finally:
        loop.run_until_complete(runner.cleanup())
        print("STATUS:stopped:Agent terminated")


if __name__ == "__main__":
    main()
