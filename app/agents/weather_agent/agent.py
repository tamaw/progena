import os
import sys
import time
import argparse
from datetime import datetime, timezone
import re
from typing import Optional, Tuple, Dict
import aiohttp
from aiohttp import web
import asyncio
import requests


# Custom exceptions used by process() per spec
class LocationParseError(Exception):
    pass

class InvalidLocationError(Exception):
    pass

class APIError(Exception):
    pass


class WeatherAgent:
    AGENT_ID = "weather_agent"
    AGENT_NAME = "Weather Agent"
    DESCRIPTION = "Provides real-time weather conditions and forecasts with clear, user-friendly explanations."
    CAPABILITIES = [
        "current_weather",
        "weather_forecast",
        "location_weather_lookup",
        "weather_explanation",
    ]

    WEATHERCODE_MAP = {
        0: "clear skies",
        1: "mainly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "fog",
        48: "depositing rime fog",
        51: "light drizzle",
        53: "moderate drizzle",
        55: "dense drizzle",
        61: "light rain",
        63: "moderate rain",
        65: "heavy rain",
        71: "light snow",
        73: "moderate snow",
        75: "heavy snow",
        80: "rain showers",
        81: "moderate rain showers",
        82: "violent rain showers",
        95: "thunderstorm",
        96: "thunderstorm with slight hail",
        99: "thunderstorm with heavy hail",
    }

    def __init__(self, api_key: Optional[str] = None, port: int = 8080):
        self.api_key = api_key or os.environ.get("WEATHER_API_KEY")
        self.start_time = time.time()
        self.port = port
        self.app = web.Application()
        self.app.add_routes([
            web.get("/.well-known/agent.json", self.agent_card),
            web.get("/health", self.health),
            web.post("/", self.handle_request),
        ])

    def _log_status(self, status: str):
        print(status)
        sys.stdout.flush()

    def uptime_seconds(self) -> float:
        return time.time() - self.start_time

    async def agent_card(self, request):
        card = {
            "name": self.AGENT_NAME,
            "description": self.DESCRIPTION,
            "version": "0.1.0",
            "protocol": "a2a",
            "capabilities": self.CAPABILITIES,
            "endpoint": f"http://localhost:{self.port}",
        }
        return web.json_response(card)

    async def health(self, request):
        payload = {
            "status": "healthy",
            "name": self.AGENT_NAME,
            "version": "0.1.0",
            "uptime_seconds": round(self.uptime_seconds(), 2),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return web.json_response(payload)

    def _extract_location_and_timeframe(self, text: str) -> Tuple[Optional[str], str, str]:
        t = text.lower()
        # timeframe
        if any(k in t for k in ["right now", "now", "currently"]):
            timeframe = "now"
        elif "tomorrow" in t:
            timeframe = "tomorrow"
        elif re.search(r"next\s+\d+\s+days", t):
            m = re.search(r"next\s+(\d+)\s+days", t)
            days = int(m.group(1))
            timeframe = f"next_{min(days,7)}_days"
        elif "this week" in t or "next week" in t or "this coming week" in t:
            timeframe = "next_7_days"
        elif "today" in t or "later today" in t:
            timeframe = "today"
        else:
            timeframe = "now"

        # simple location extraction: look for 'in <location>' or 'at <location>'
        m = re.search(r"\b(in|at)\s+([A-Za-z\s,.'-]+?)(?:\b(?:today|tomorrow|now|this week|next week|this coming week|right now|currently|$))", text, re.IGNORECASE)
        if m:
            location = m.group(2).strip().strip(',')
            return location, timeframe, text
        # fallback: try for 'City, Country' pattern
        m2 = re.search(r"([A-Za-z\s]+,\s*[A-Za-z\s]{2,})", text)
        if m2:
            return m2.group(1).strip(), timeframe, text
        # none found
        return None, timeframe, text

    async def _geocode(self, session: aiohttp.ClientSession, location: str) -> Optional[Tuple[float, float, str]]:
        # Use Open-Meteo geocoding (no API key required) for broader compatibility
        url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {"name": location, "count": 1, "language": "en", "format": "json"}
        try:
            async with session.get(url, params=params, timeout=10) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                if not data:
                    return None
                item = data[0]
                name_parts = [item.get("name")]
                if item.get("state"):
                    name_parts.append(item.get("state"))
                if item.get("country"):
                    name_parts.append(item.get("country"))
                pretty = ", ".join([p for p in name_parts if p])
                return float(item["lat"]), float(item["lon"]), pretty
        except Exception:
            return None

    async def _fetch_weather(self, session: aiohttp.ClientSession, lat: float, lon: float, units: str = "metric") -> Optional[dict]:
        # Use Open-Meteo forecast (no API key required)
        url = "https://api.open-meteo.com/v1/forecast"
        params = {"latitude": lat, "longitude": lon, "timezone": "UTC"}
        if units == "metric":
            params.update({"temperature_unit": "celsius", "windspeed_unit": "kmh"})
        else:
            params.update({"temperature_unit": "fahrenheit", "windspeed_unit": "mph"})
        params["current_weather"] = "true"
        params["daily"] = "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
        params["hourly"] = "temperature_2m,precipitation,weathercode,windspeed_10m,precipitation_probability"
        params["forecast_days"] = 7
        try:
            async with session.get(url, params=params, timeout=10) as r:
                if r.status != 200:
                    return None
                return await r.json()
        except Exception:
            return None

    def _format_current(self, location_name: str, weather: dict, units: str) -> str:
        cur = weather.get("current", {})
        desc = cur.get("weather", [{}])[0].get("description", "").lower()
        temp = cur.get("temp")
        feels = cur.get("feels_like")
        wind = cur.get("wind_speed")
        # precipitation
        precip = None
        if "rain" in cur:
            precip = cur.get("rain", {}).get("1h", 0)
        elif "snow" in cur:
            precip = cur.get("snow", {}).get("1h", 0)
        precip_text = "None expected at the moment" if not precip else f"{precip} mm in the last hour"

        unit_sym = "°C" if units == "metric" else "°F"
        wind_unit = "km/h" if units == "metric" else "mph"
        if units == "metric" and wind is not None:
            wind_kmh = round(wind * 3.6)
            wind_text = f"Light breeze at {wind_kmh} {wind_unit}" if wind_kmh < 20 else f"Winds around {wind_kmh} {wind_unit}"
        elif wind is not None:
            wind_text = f"Light breeze at {round(wind)} {wind_unit}" if wind < 20 else f"Winds around {round(wind)} {wind_unit}"
        else:
            wind_text = "Calm"

        lines = []
        lines.append(f"Right now in {location_name}, it’s {round(temp)}{unit_sym} with {desc}.")
        lines.append("")
        lines.append(f"🌡 Temperature: {round(temp)}{unit_sym} (feels like {round(feels)}{unit_sym})")
        lines.append(f"💨 Wind: {wind_text}")
        lines.append(f"🌧 Precipitation: {precip_text}")
        lines.append("")
        lines.append("Overall, it’s a comfortable time to be outside." if temp and temp>5 else "Overall, it’s a cool time to be outside.")
        return "\n".join(lines)

    def _format_forecast_day(self, day: dict, units: str) -> Tuple[str, int, int, int]:
        cond = day.get("weather", [{}])[0].get("description", "").capitalize()
        temp_max = round(day.get("temp", {}).get("max", 0))
        temp_min = round(day.get("temp", {}).get("min", 0))
        pop = int(day.get("pop", 0) * 100)
        return cond, temp_max, temp_min, pop

    def _format_today(self, location_name: str, weather: dict, units: str) -> str:
        daily = weather.get("daily", [])
        if not daily:
            raise Exception("No daily data")
        d = daily[0]
        cond, temp_max, temp_min, pop = self._format_forecast_day(d, units)
        unit_sym = "°C" if units == "metric" else "°F"
        wind = d.get("wind_speed")
        wind_unit = "km/h" if units == "metric" else "mph"
        if units == "metric" and wind is not None:
            wind_kmh = round(wind * 3.6)
        else:
            wind_kmh = round(wind) if wind is not None else None
        lines = []
        lines.append(f"Here’s the forecast for {location_name} today:")
        lines.append("")
        lines.append(f"🌤 Conditions: {cond}")
        lines.append(f"🌡 High / Low: {temp_max}{unit_sym} / {temp_min}{unit_sym}")
        lines.append(f"💨 Wind: Gentle winds around {wind_kmh} {wind_unit}")
        lines.append(f"🌧 Chance of rain: {pop}%")
        lines.append("")
        lines.append("It should be a pleasant day, great for outdoor activities." if pop<30 else "It might be wet, consider carrying an umbrella.")
        return "\n".join(lines)

    def _format_tomorrow(self, location_name: str, weather: dict, units: str) -> str:
        daily = weather.get("daily", [])
        if len(daily) < 2:
            raise Exception("No tomorrow data")
        d = daily[1]
        cond, temp_max, temp_min, pop = self._format_forecast_day(d, units)
        unit_sym = "°C" if units == "metric" else "°F"
        wind = d.get("wind_speed")
        wind_unit = "km/h" if units == "metric" else "mph"
        if units == "metric" and wind is not None:
            wind_kmh = round(wind * 3.6)
        else:
            wind_kmh = round(wind) if wind is not None else None
        lines = []
        lines.append(f"Here’s the forecast for {location_name} tomorrow:")
        lines.append("")
        lines.append(f"🌤 Conditions: {cond}")
        lines.append(f"🌡 High / Low: {temp_max}{unit_sym} / {temp_min}{unit_sym}")
        lines.append(f"💨 Wind: Gentle winds around {wind_kmh} {wind_unit}")
        lines.append(f"🌧 Chance of rain: {pop}%")
        lines.append("")
        lines.append("It should be a pleasant day, great for outdoor activities." if pop<30 else "It might be wet, consider carrying an umbrella.")
        return "\n".join(lines)

    def _format_multi_day(self, location_name: str, weather: dict, units: str, days: int) -> str:
        daily = weather.get("daily", [])
        days = min(days, len(daily)-1, 7)
        if days <= 0:
            raise Exception("No multi-day data")
        lines = []
        lines.append(f"Here’s the forecast for {location_name} for the next {days} days:")
        lines.append("")
        for i in range(1, days+1):
            d = daily[i]
            dt = datetime.fromtimestamp(d.get("dt", 0)).strftime("%a")
            cond, temp_max, temp_min, pop = self._format_forecast_day(d, units)
            unit_sym = "°C" if units == "metric" else "°F"
            lines.append(f"{dt}: {cond} — {temp_max}{unit_sym}/{temp_min}{unit_sym}, {pop}% chance of rain")
        lines.append("")
        lines.append("Pack layers and check daily updates as conditions can change.")
        return "\n".join(lines)

    def _geocode_sync(self, location: str):
        try:
            url = "https://geocoding-api.open-meteo.com/v1/search"
            params = {"name": location, "count": 1, "language": "en", "format": "json"}
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            if not data or "results" not in data or not data["results"]:
                return None
            item = data["results"][0]
            pretty = item.get("name")
            if item.get("country_code"):
                pretty = f"{pretty}, {item.get('country_code')}"
            return float(item["latitude"]), float(item["longitude"]), pretty
        except Exception:
            return None

    def _fetch_weather_sync(self, lat: float, lon: float, units: str = "metric"):
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {"latitude": lat, "longitude": lon, "timezone": "UTC"}
            if units == "metric":
                params.update({"temperature_unit": "celsius", "windspeed_unit": "kmh"})
            else:
                params.update({"temperature_unit": "fahrenheit", "windspeed_unit": "mph"})
            params["current_weather"] = "true"
            params["daily"] = "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode,temperature_2m_mean"
            params["hourly"] = "temperature_2m,precipitation,weathercode,windspeed_10m,precipitation_probability"
            params["forecast_days"] = 7
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def process(self, req_text: str, context: Dict) -> str:
        # Determine units
        units = (context.get("units") or "metric").lower()
        if units not in ("metric", "imperial"):
            units = "metric"

        # Location resolution
        location = context.get("location")
        extracted_loc, timeframe, _ = self._extract_location_and_timeframe(req_text)
        if not location:
            location = extracted_loc

        if not location:
            # ask clarification
            context["_metadata"] = {"needs_location": True}
            return "I can help with that — which city or location would you like the weather for?"

        # days parsing
        days = 1
        if timeframe.startswith("next_"):
            m = re.search(r"next_(\d+)_days", timeframe)
            if m:
                days = min(int(m.group(1)), 7)
                timeframe_key = "daily"
        elif timeframe == "tomorrow":
            days = 1
            timeframe_key = "daily"
        elif timeframe in ("today",):
            timeframe_key = "daily"
        elif timeframe == "now":
            timeframe_key = "current"
        else:
            timeframe_key = "current"

        # Geocode
        geo = self._geocode_sync(location)
        if not geo:
            raise InvalidLocationError()
        lat, lon, pretty = geo

        # Fetch weather
        data = self._fetch_weather_sync(lat, lon, units=units)
        if not data:
            raise APIError()

        # Build response
        unit_sym = "°C" if units == "metric" else "°F"
        try:
            if timeframe_key == "current":
                cw = data.get("current_weather") or {}
                temp = cw.get("temperature")
                wind = cw.get("windspeed")
                code = int(cw.get("weathercode")) if cw.get("weathercode") is not None else None
                cond = self.WEATHERCODE_MAP.get(code, "variable conditions") if code is not None else "variable conditions"
                resp = f"Right now in {pretty}, it’s {round(temp)}{unit_sym} with {cond}. Winds are around {round(wind)} {'km/h' if units=='metric' else 'mph'}."
                context["_metadata"] = {"location": pretty, "timeframe": "current", "units": units, "data_source": "open-meteo"}
                return resp
            else:
                # daily or multi-day
                daily = data.get("daily", {})
                temps_min = daily.get("temperature_2m_min", [])
                temps_max = daily.get("temperature_2m_max", [])
                wcodes = daily.get("weathercode", [])
                pprobs = daily.get("precipitation_probability_max", [])
                if days == 1:
                    idx = 1 if len(temps_max) > 1 else 0
                    tmin = temps_min[idx] if idx < len(temps_min) else None
                    tmax = temps_max[idx] if idx < len(temps_max) else None
                    code = int(wcodes[idx]) if idx < len(wcodes) else None
                    cond = self.WEATHERCODE_MAP.get(code, "variable conditions") if code is not None else "variable conditions"
                    pprob = int(pprobs[idx]) if idx < len(pprobs) else None
                    note = f"There is a {pprob}% chance of precipitation." if pprob is not None else "Precipitation chances are unclear."
                    resp = f"Tomorrow in {pretty}, temperatures will range from {round(tmin)}{unit_sym} to {round(tmax)}{unit_sym}. Expect {cond}. {note}"
                    context["_metadata"] = {"location": pretty, "timeframe": "daily", "units": units}
                    return resp
                else:
                    days_available = min(days, len(temps_max)) if temps_max else days
                    if not temps_max:
                        resp = f"Forecast data for the next {days} days in {pretty} is not available."
                        context["_metadata"] = {"location": pretty, "timeframe": "daily", "units": units}
                        return resp
                    tmin = min(temps_min[:days_available])
                    tmax = max(temps_max[:days_available])
                    trend = "steady"
                    if temps_max[0] < temps_max[min(days_available-1, len(temps_max)-1)]:
                        trend = "warming"
                    elif temps_max[0] > temps_max[min(days_available-1, len(temps_max)-1)]:
                        trend = "cooling"
                    precip_risk = any((p or 0) >= 30 for p in (pprobs[:days_available] or []))
                    precip_text = "rain is likely by the end of the period" if precip_risk else "mostly dry"
                    resp = f"Over the next {days_available} days in {pretty}, temperatures will range from {round(tmin)}{unit_sym} to {round(tmax)}{unit_sym}. Expect {trend} conditions and {precip_text}."
                    context["_metadata"] = {"location": pretty, "timeframe": "daily", "units": units, "days": days_available}
                    return resp
        except Exception:
            raise APIError()

    async def handle_request(self, request):
        data = await request.json()
        req_text = data.get("request")
        context = data.get("context") or {}
        req_id = data.get("id")

        if not req_text:
            return web.json_response({
                "success": False,
                "response": None,
                "error": "I couldn’t tell which location you want the weather for. Please specify a city or place.",
                "metadata": {},
            })

        # Call process in executor to avoid blocking the event loop
        self._log_status("STATUS:executing:Processing request")
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        try:
            if loop and loop.is_running():
                result = await asyncio.get_event_loop().run_in_executor(None, self.process, req_text, context)
            else:
                result = self.process(req_text, context)

            # process should populate context['_metadata']
            metadata = context.get("_metadata", {})
            self._log_status("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({"success": True, "response": result, "error": None, "metadata": metadata})
        except LocationParseError:
            self._log_status("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({"success": False, "response": None, "error": "I couldn’t tell which location you want the weather for. Please specify a city or place.", "metadata": {}})
        except InvalidLocationError:
            self._log_status("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({"success": False, "response": None, "error": "I couldn’t find weather data for that location. Please check the name and try again.", "metadata": {}})
        except APIError:
            self._log_status("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({"success": False, "response": None, "error": "Weather data is temporarily unavailable. Please try again later.", "metadata": {}})
        except Exception as e:
            self._log_status(f"STATUS:failed:{str(e)}")
            self._log_status("STATUS:waiting:Idle, waiting for requests")
            return web.json_response({"success": False, "response": None, "error": "Weather data is temporarily unavailable. Please try again later.", "metadata": {}})


async def run_server(port: int = 8080):
    agent = WeatherAgent(port=port)
    agent._log_status("STATUS:starting:Initializing Weather Agent")
    runner = web.AppRunner(agent.app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    agent._log_status(f"STATUS:running:Server started on port {port}")
    agent._log_status("STATUS:waiting:Idle, waiting for requests")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    args = parser.parse_args()
    port = args.port
    try:
        asyncio.run(run_server(port=port))
    except KeyboardInterrupt:
        print("STATUS:stopping:Shutting down")
        print("STATUS:stopped:Agent terminated")
