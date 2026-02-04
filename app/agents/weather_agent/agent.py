import asyncio
import json
import time
import math
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List

from aiohttp import web

# Module-level start time for uptime calculations
START_TIME = time.time()

# Simple in-memory cache
_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # seconds
_CACHE_MAX = 200


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, val = entry
    if time.time() - ts > _CACHE_TTL:
        del _CACHE[key]
        return None
    return val


def _cache_set(key: str, val: Any):
    if len(_CACHE) >= _CACHE_MAX:
        # remove oldest
        oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])[0]
        del _CACHE[oldest]
    _CACHE[key] = (time.time(), val)


class WeatherAgent:
    AGENT_ID = "weather_agent"
    AGENT_NAME = "Weather Agent"
    DESCRIPTION = "Provides current weather conditions and forecasts for any location"
    CAPABILITIES = [
        "current_weather",
        "hourly_forecast",
        "daily_forecast",
        "location_weather_lookup",
        "weather_summary"
    ]

    VERSION = "0.1.0"

    def __init__(self):
        pass

    @staticmethod
    def _intent_from_text(text: str) -> Tuple[str, int]:
        t = (text or "").lower()
        days = 1
        if "hour" in t or "hourly" in t:
            return "hourly_forecast", 0
        if "summary" in t:
            return "weather_summary", 1
        # multi-day like "5 day" or "5-day"
        import re

        m = re.search(r"(\d+)\s*-?\s*day", t)
        if m:
            try:
                days = int(m.group(1))
                days = max(1, min(days, 7))
            except Exception:
                days = 1
            return "daily_forecast", days
        if "forecast" in t:
            # today or default
            if "today" in t:
                return "daily_forecast", 1
            if "tomorrow" in t:
                return "daily_forecast", 2
            return "daily_forecast", 1
        if "weather" in t or "temperature" in t or "rain" in t or "snow" in t:
            return "current_weather", 1
        return "unsupported", 0

    @staticmethod
    def _extract_location(request_text: str, context: Dict[str, Any]) -> str:
        # Priority: context.location
        if context and isinstance(context, dict):
            loc = context.get("location")
            if loc:
                return loc
        # Try simple extraction: look for " in <location>"
        if not request_text:
            return ""
        low = request_text.lower()
        if " in " in low:
            # naive extraction
            parts = request_text.split(" in ", 1)[1]
            # stop at keywords
            for stop in [" today", " tomorrow", " forecast", " hourly", " summary"]:
                if stop in parts.lower():
                    parts = parts.lower().split(stop, 1)[0]
                    break
            return parts.strip().strip(".?")
        return ""

    @staticmethod
    def _unit_labels(units: str) -> Tuple[str, str]:
        if units == "imperial":
            return "°F", "mph"
        return "°C", "km/h"

    @staticmethod
    def _convert_temp_c_to_f(c: float) -> float:
        return c * 9.0 / 5.0 + 32.0

    @staticmethod
    def _convert_speed_kph_to_mph(kph: float) -> float:
        return kph * 0.621371

    @staticmethod
    def _deterministic_randints(seed_str: str, count: int, bounds: Tuple[int, int]) -> List[int]:
        # Use SHA256 to generate deterministic bytes
        h = hashlib.sha256(seed_str.encode("utf-8")).digest()
        vals = []
        i = 0
        while len(vals) < count:
            b = h[i % len(h)]
            # expand using new hash
            h = hashlib.sha256(h + bytes([b])).digest()
            vals.append(bounds[0] + (b % (bounds[1] - bounds[0] + 1)))
            i += 1
        return vals

    @classmethod
    def _simulate_current(cls, location: str, units: str) -> Dict[str, Any]:
        # deterministic values based on location and current day
        key_seed = f"current:{location}:{datetime.utcnow().date().isoformat()}"
        r = cls._deterministic_randints(key_seed, 6, (0, 100))
        # map to realistic ranges
        temp_c =  -5 + (r[0] / 100.0) * 45.0  # -5 to 40
        feels = temp_c - 2 + (r[1] / 100.0) * 4.0
        cond_idx = r[2] % 6
        cond_map = ["Clear", "Partly Cloudy", "Cloudy", "Light Rain", "Rain", "Snow"]
        condition = cond_map[cond_idx]
        precip = int(r[3])  # 0-100
        wind_kph = 5 + int(r[4] / 100.0 * 60)  # 5-65
        humidity = int(20 + r[5] / 100.0 * 80)  # 20-100

        if units == "imperial":
            temp = round(cls._convert_temp_c_to_f(temp_c))
            feels_like = round(cls._convert_temp_c_to_f(feels))
            wind = round(cls._convert_speed_kph_to_mph(wind_kph))
        else:
            temp = round(temp_c)
            feels_like = round(feels)
            wind = round(wind_kph)

        return {
            "temperature": temp,
            "feels_like": feels_like,
            "condition": condition,
            "precipitation_chance": precip,
            "wind": wind,
            "humidity": humidity,
        }

    @classmethod
    def _simulate_daily_forecast(cls, location: str, days: int, units: str) -> List[Dict[str, Any]]:
        forecasts = []
        for d in range(days):
            date = datetime.utcnow().date() + timedelta(days=d)
            seed = f"daily:{location}:{date.isoformat()}"
            r = cls._deterministic_randints(seed, 4, (0, 100))
            high_c = 5 + (r[0] / 100.0) * 30  # 5-35
            low_c = max(-5, high_c - (5 + (r[1] / 100.0) * 10))
            cond_map = ["Sunny", "Partly Cloudy", "Cloudy", "Light Rain", "Rain", "Snow"]
            condition = cond_map[r[2] % len(cond_map)]
            precip = int(r[3])
            if units == "imperial":
                high = round(cls._convert_temp_c_to_f(high_c))
                low = round(cls._convert_temp_c_to_f(low_c))
            else:
                high = round(high_c)
                low = round(low_c)
            forecasts.append({
                "date": date.isoformat(),
                "high": high,
                "low": low,
                "condition": condition,
                "precipitation_chance": precip,
            })
        return forecasts

    @classmethod
    def _simulate_hourly_forecast(cls, location: str, hours: int, units: str) -> List[Dict[str, Any]]:
        forecasts = []
        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        for h in range(hours):
            ts = now + timedelta(hours=h + 1)
            seed = f"hourly:{location}:{ts.isoformat()}"
            r = cls._deterministic_randints(seed, 3, (0, 100))
            temp_c = -5 + (r[0] / 100.0) * 45
            cond_map = ["Sunny", "Partly Cloudy", "Cloudy", "Light Rain", "Rain", "Snow"]
            condition = cond_map[r[1] % len(cond_map)]
            precip = int(r[2])
            if units == "imperial":
                temp = round(cls._convert_temp_c_to_f(temp_c))
            else:
                temp = round(temp_c)
            forecasts.append({
                "time": ts.strftime("%H:%M"),
                "temp": temp,
                "condition": condition,
                "precipitation_chance": precip,
            })
        return forecasts

    @classmethod
    def process(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            text = payload.get("request", "") if isinstance(payload, dict) else ""
            context = payload.get("context", {}) if isinstance(payload, dict) else {}
            units = context.get("units", "metric") if isinstance(context, dict) else "metric"
            if units not in ("metric", "imperial"):
                units = "metric"
            forecast_days = int(context.get("forecast_days", 1)) if isinstance(context, dict) else 1
            forecast_days = max(1, min(7, forecast_days))

            intent, days_from_text = cls._intent_from_text(text)
            if intent == "unsupported":
                # If context explicitly requests weather but text is unsupported, still treat as unsupported
                return {
                    "success": False,
                    "response": None,
                    "error": "Unsupported request. This agent only handles weather-related queries.",
                    "metadata": {},
                }

            # allow explicit days in text to override
            if intent == "daily_forecast" and days_from_text:
                forecast_days = days_from_text

            location = cls._extract_location(text, context)
            if not location:
                return {
                    "success": False,
                    "response": None,
                    "error": "Location not specified. Please provide a city, region, or coordinates.",
                    "metadata": {},
                }

            # caching key
            cache_key = f"{intent}|{location}|{units}|{forecast_days}"
            cached = _cache_get(cache_key)
            if cached:
                return cached

            # Generate data
            if intent == "current_weather":
                data = cls._simulate_current(location, units)
                unit_temp, unit_wind = cls._unit_labels(units)
                response_text = (
                    f"🌤️ Current Weather for {location}\n\n"
                    f"Temperature: {data['temperature']}{unit_temp} (feels like {data['feels_like']}{unit_temp})\n"
                    f"Condition: {data['condition']}\n"
                    f"Humidity: {data['humidity']}%\n"
                    f"Wind: {data['wind']} {unit_wind}\n"
                    f"Precipitation Chance: {data['precipitation_chance']}%"
                )
                envelope = {
                    "success": True,
                    "response": response_text,
                    "error": None,
                    "metadata": {
                        "location": location,
                        "units": units,
                        "forecast_days": 1,
                        "data_source": "simulated",
                    },
                }
                _cache_set(cache_key, envelope)
                return envelope

            if intent == "daily_forecast":
                days = forecast_days
                data = cls._simulate_daily_forecast(location, days, units)
                unit_temp, _ = cls._unit_labels(units)
                header = f"📅 {days}-Day Forecast for {location}\n\n"
                body_lines = []
                for idx, day in enumerate(data):
                    if idx == 0:
                        day_label = "Today"
                    elif idx == 1:
                        day_label = "Tomorrow"
                    else:
                        day_label = f"Day {idx + 1}"
                    lines = (
                        f"{day_label}:\n"
                        f"  High: {day['high']}{unit_temp} • Low: {day['low']}{unit_temp}\n"
                        f"  Condition: {day['condition']}\n"
                        f"  Precipitation Chance: {day['precipitation_chance']}%\n"
                    )
                    body_lines.append(lines)
                response_text = header + "\n".join(body_lines).strip()
                envelope = {
                    "success": True,
                    "response": response_text,
                    "error": None,
                    "metadata": {
                        "location": location,
                        "units": units,
                        "forecast_days": days,
                        "data_source": "simulated",
                    },
                }
                _cache_set(cache_key, envelope)
                return envelope

            if intent == "hourly_forecast":
                hours = 6
                data = cls._simulate_hourly_forecast(location, hours, units)
                header = f"🕒 Hourly Forecast for {location}\n\n"
                lines = []
                for h in data:
                    lines.append(f"{h['time']} — {h['temp']}{cls._unit_labels(units)[0]}, {h['condition']}")
                response_text = header + "\n".join(lines)
                envelope = {
                    "success": True,
                    "response": response_text,
                    "error": None,
                    "metadata": {
                        "location": location,
                        "units": units,
                        "forecast_days": 1,
                        "data_source": "simulated",
                    },
                }
                _cache_set(cache_key, envelope)
                return envelope

            if intent == "weather_summary":
                # Provide a short summary using daily forecast for 2 days
                data = cls._simulate_daily_forecast(location, 2, units)
                summary_lines = []
                for idx, day in enumerate(data):
                    if idx == 0:
                        label = "Today"
                    else:
                        label = "Tomorrow"
                    summary_lines.append(f"{label}: {day['condition']} with highs around {day['high']}{cls._unit_labels(units)[0]}")
                response_text = "\n".join(summary_lines)
                envelope = {
                    "success": True,
                    "response": response_text,
                    "error": None,
                    "metadata": {
                        "location": location,
                        "units": units,
                        "forecast_days": 2,
                        "data_source": "simulated",
                    },
                }
                _cache_set(cache_key, envelope)
                return envelope

            # fallback
            return {
                "success": False,
                "response": None,
                "error": "Unsupported request. This agent only handles weather-related queries.",
                "metadata": {},
            }

        except Exception as e:
            print(f"STATUS:failed:{str(e)}")
            return {
                "success": False,
                "response": None,
                "error": "Internal weather service error",
                "metadata": {},
            }


# HTTP server

def create_app(port: int = 8080) -> web.Application:
    app = web.Application()
    agent = WeatherAgent()

    async def agent_card(request: web.Request):
        endpoint = f"http://localhost:{port}"
        card = {
            "name": WeatherAgent.AGENT_NAME,
            "description": WeatherAgent.DESCRIPTION,
            "version": WeatherAgent.VERSION,
            "protocol": "a2a",
            "capabilities": [
                "current_weather",
                "hourly_forecast",
                "daily_forecast",
                "location_weather_lookup",
                "weather_summary"
            ],
            "endpoint": endpoint,
        }
        return web.json_response(card)

    async def health(request: web.Request):
        uptime = time.time() - START_TIME
        ts = datetime.utcnow().isoformat() + "Z"
        data = {
            "status": "healthy",
            "name": WeatherAgent.AGENT_NAME,
            "version": WeatherAgent.VERSION,
            "uptime_seconds": round(uptime, 2),
            "timestamp": ts,
        }
        return web.json_response(data)

    async def handle_post(request: web.Request):
        print("STATUS:executing:Processing request")
        try:
            try:
                payload = await request.json()
            except json.JSONDecodeError:
                return web.json_response({
                    "success": False,
                    "response": None,
                    "error": "Malformed JSON in request",
                    "metadata": {},
                }, status=400)
            except Exception:
                # aiohttp raises ContentTypeError if not json
                text = await request.text()
                try:
                    payload = json.loads(text)
                except Exception:
                    return web.json_response({
                        "success": False,
                        "response": None,
                        "error": "Malformed JSON in request",
                        "metadata": {},
                    }, status=400)

            resp = WeatherAgent.process(payload)
            status_code = 200
            if not resp.get("success") and resp.get("error") == "Location not specified. Please provide a city, region, or coordinates.":
                status_code = 400
            if not resp.get("success") and resp.get("error") == "Unsupported request. This agent only handles weather-related queries.":
                status_code = 400
            if not resp.get("success") and resp.get("error") == "Internal weather service error":
                status_code = 500
            return web.json_response(resp, status=status_code)
        except Exception as e:
            print(f"STATUS:failed:{str(e)}")
            return web.json_response({
                "success": False,
                "response": None,
                "error": "Internal weather service error",
                "metadata": {},
            }, status=500)

    app.router.add_get("/.well-known/agent.json", agent_card)
    app.router.add_get("/health", health)
    app.router.add_post("/", handle_post)
    return app


async def run_app(port: int = 8080):
    print("STATUS:starting:Initializing Weather Agent")
    app = create_app(port)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"STATUS:running:Server started on port {port}")
    print("STATUS:waiting:Idle, waiting for requests")
    # run forever
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    import os

    port = int(os.environ.get("AGENT_PORT", "8080"))
    try:
        asyncio.run(run_app(port))
    except KeyboardInterrupt:
        print("STATUS:stopping:Shutting down")
        print("STATUS:stopped:Agent terminated")
