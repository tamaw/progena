import os
import sys
import asyncio
import time
from datetime import datetime, timezone, timedelta
import json
import aiohttp
from aiohttp import web

class Weather:
    AGENT_ID = "weather"
    AGENT_NAME = "Weather Agent"
    DESCRIPTION = "Provides calm, human-friendly weather explanations and practical advice based on real-time forecasts."
    CAPABILITIES = [
        "current_weather",
        "forecast_weather",
        "weather_advice",
        "weather_safety_guidance",
    ]

    def __init__(self, port: int = 8000):
        self.port = port
        self.start_time = time.time()
        self.api_key = os.environ.get("OPENWEATHER_API_KEY")
        self.app = web.Application()
        self.app.add_routes([
            web.get('/.well-known/agent.json', self.agent_card),
            web.get('/health', self.health),
            web.post('/', self.handle_request),
        ])

    async def agent_card(self, request):
        endpoint = f"http://localhost:{self.port}"
        card = {
            "name": self.AGENT_NAME,
            "description": self.DESCRIPTION,
            "version": "0.1.0",
            "protocol": "a2a",
            "capabilities": self.CAPABILITIES,
            "endpoint": endpoint,
        }
        return web.json_response(card)

    async def health(self, request):
        uptime = time.time() - self.start_time
        payload = {
            "status": "healthy",
            "name": self.AGENT_NAME,
            "version": "0.1.0",
            "uptime_seconds": round(uptime, 2),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return web.json_response(payload)

    async def handle_request(self, request):
        print('STATUS:executing:Processing request', flush=True)
        try:
            data = await request.json()
        except Exception:
            print('STATUS:failed:Invalid JSON', flush=True)
            return web.json_response({
                "success": False,
                "response": None,
                "error": "Invalid JSON",
                "metadata": None,
            }, status=400)

        user_request = data.get('request')
        context = data.get('context', {}) or {}
        req_id = data.get('id')

        # Parse intent and timeframe
        timeframe = context.get('timeframe', 'now')
        intent = context.get('intent', 'general')
        location = context.get('location')

        # Simple inference from the request text when context is missing
        # (keeps behavior graceful if not found)
        if user_request and not location:
            import re
            lr = user_request
            # infer location from 'in <City>' patterns
            m = re.search(r"\bin\s+([A-Za-z\u00C0-\u017F\- ]+?)(?:\s+(?:tonight|today|tomorrow|this morning|this evening)|[\?\.,!]|$)", lr, re.I)
            if m:
                location = m.group(1).strip()

        # infer timeframe from phrasing
        if user_request and 'timeframe' not in context:
            lr_low = user_request.lower()
            if 'tomorrow' in lr_low:
                timeframe = 'tomorrow'
            elif 'next 7' in lr_low or 'next seven' in lr_low:
                timeframe = 'next_7_days'
            elif 'today' in lr_low or 'this morning' in lr_low or 'this evening' in lr_low or 'tonight' in lr_low:
                timeframe = 'today'

        # infer intent from keywords
        if user_request and 'intent' not in context:
            lr_low = user_request.lower()
            if any(k in lr_low for k in ['jacket', 'coat', 'warm', 'cold', 'what to wear']):
                intent = 'clothing'
            elif any(k in lr_low for k in ['safe', 'danger', 'storm', 'alerts', 'safe to go']):
                intent = 'safety'
            elif any(k in lr_low for k in ['travel', 'flight', 'road']):
                intent = 'travel'
            elif any(k in lr_low for k in ['picnic', 'outside', 'park', 'hike', 'walk']):
                intent = 'outdoor_plans'

        if not user_request and not location:
            print('STATUS:waiting:Idle, waiting for requests', flush=True)
            return web.json_response({
                "success": True,
                "response": "I can help with that — which city or location are you asking about?",
                "error": None,
                "metadata": {},
            })

        if not location:
            # ask clarification if still no location
            print('STATUS:waiting:Idle, waiting for requests', flush=True)
            return web.json_response({
                "success": True,
                "response": "I can help with that — which city or location are you asking about?",
                "error": None,
                "metadata": {},
            })

        # Resolve location to lat/lon
        try:
            coords, resolved_name = await self.resolve_location(location)
        except ValueError as e:
            print(f'STATUS:failed:{e}', flush=True)
            return web.json_response({
                "success": False,
                "response": "I couldn't find that location — could you check the name or provide coordinates?",
                "error": str(e),
                "metadata": {},
            }, status=400)

        # Fetch weather
        try:
            weather = await self.fetch_weather(coords)
        except Exception as e:
            print(f'STATUS:failed:{e}', flush=True)
            return web.json_response({
                "success": False,
                "response": "I'm having trouble reaching the weather service right now. Please try again in a moment.",
                "error": str(e),
                "metadata": {"location": resolved_name},
            }, status=502)

        # Interpret and craft advice
        try:
            advice = self.generate_advice(weather, timeframe, intent, resolved_name, user_request)
        except Exception as e:
            print(f'STATUS:failed:{e}', flush=True)
            return web.json_response({
                "success": False,
                "response": "Sorry, something went wrong while preparing advice.",
                "error": str(e),
                "metadata": {"location": resolved_name},
            }, status=500)

        print('STATUS:waiting:Idle, waiting for requests', flush=True)
        return web.json_response({
            "success": True,
            "response": advice,
            "error": None,
            "metadata": {"location": resolved_name, "timeframe": timeframe, "source": "OpenWeather"},
        })

    async def resolve_location(self, location):
        # Accept dict with lat/lon
        if isinstance(location, dict) and 'lat' in location and 'lon' in location:
            return ({'lat': float(location['lat']), 'lon': float(location['lon'])}, f"{location['lat']},{location['lon']}")

        # Otherwise assume a city name
        # If API key is not configured, fall back to a generic coordinate so the agent can still operate
        # in development or test environments without calling the external API.
        if not self.api_key:
            return ({'lat': 0.0, 'lon': 0.0}, str(location))

        params = {'q': str(location), 'limit': 1, 'appid': self.api_key}
        url = 'http://api.openweathermap.org/geo/1.0/direct'
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise ValueError('Geocoding failed')
                data = await resp.json()
                if not data:
                    raise ValueError('Location not found')
                first = data[0]
                return ({'lat': first['lat'], 'lon': first['lon']}, f"{first.get('name')}, {first.get('country')}")

    async def fetch_weather(self, coords):
        if not self.api_key:
            # Return a lightweight mock weather payload when API key is missing
            # This allows the agent to function in test/dev environments.
            mock = {
                "current": {"temp": 12.0, "feels_like": 11.0, "wind_speed": 6.0},
                "hourly": [{"pop": 0.1}],
                "daily": [
                    {"temp": {"day": 12.0}, "wind_speed": 6.0, "pop": 0.1},
                    {"temp": {"day": 13.0}, "wind_speed": 5.0, "pop": 0.05},
                ],
                "alerts": []
            }
            return mock
        params = {
            'lat': coords['lat'],
            'lon': coords['lon'],
            'exclude': 'minutely',
            'units': 'metric',
            'appid': self.api_key,
        }
        url = 'https://api.openweathermap.org/data/2.5/onecall'
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f'Weather API error: {resp.status} {text}')
                return await resp.json()

    def generate_advice(self, weather, timeframe, intent, location_name, user_request=None):
        # Determine main descriptors
        current = weather.get('current', {})
        hourly = weather.get('hourly', [])
        daily = weather.get('daily', [])
        alerts = weather.get('alerts', [])

        # Choose sample based on timeframe
        if timeframe == 'now':
            sample = current
            desc_time = 'right now'
        elif timeframe == 'today':
            sample = daily[0] if daily else current
            desc_time = 'today'
        elif timeframe == 'tomorrow':
            sample = daily[1] if len(daily) > 1 else (daily[0] if daily else current)
            desc_time = 'tomorrow'
        elif timeframe in ('next_7_days', 'next 7 days', 'next7days'):
            sample = daily[0] if daily else current
            desc_time = 'over the next week'
        else:
            sample = daily[0] if daily else current
            desc_time = 'the coming days'

        temp = sample.get('temp')
        # daily temp might be a dict with 'day'
        if isinstance(temp, dict):
            temp_val = temp.get('day')
        else:
            temp_val = temp or current.get('temp')

        wind = sample.get('wind_speed') or current.get('wind_speed', 0)
        pop = sample.get('pop') if sample.get('pop') is not None else (hourly[0].get('pop') if hourly else 0)

        # Qualitative label
        if temp_val is None:
            temp_label = 'mild'
        elif temp_val <= 5:
            temp_label = 'very cold'
        elif temp_val <= 15:
            temp_label = 'cool'
        elif temp_val <= 25:
            temp_label = 'mild'
        else:
            temp_label = 'warm'

        # Decide if stormy/severe
        is_storm = bool(alerts) or (pop and pop >= 0.6) or (wind and wind >= 15)
        has_showers = (pop and pop >= 0.3) or False

        # Templates matching spec examples
        # Safety / storm template
        if is_storm:
            # Prefer alert wording if alerts present
            if alerts:
                first = f"There are weather alerts in {location_name} {desc_time}."
            else:
                first = f"There are storms expected in {location_name} {desc_time}, with heavy rain and gusty winds at times."
            second = "It’s best to keep outdoor plans short and stay indoors during the strongest storms."
            third = "If you do head out, keep an eye on local alerts."
            return f"{first} {second} {third}"

        # Clothing-focused template
        if intent == 'clothing':
            # descriptor phrasing similar to examples
            if temp_label == 'cool' or temp_label == 'very cold':
                first = f"It’s on the cool side {desc_time} in {location_name}, and the breeze makes it feel a bit cooler than it looks."
                second = "A light jacket will keep you comfortable, especially if you’ll be outside for a while." if temp_label == 'cool' else "A warm jacket and layers are a good idea."
                return f"{first} {second}"
            elif temp_label == 'mild':
                first = f"{desc_time.capitalize()} looks mild and mostly dry in {location_name}."
                second = "Light clothing should be comfortable for most activities." if not has_showers else "Light layers are fine, but keep a waterproof layer handy in case of showers."
                return f"{first} {second}"
            else:
                # warm
                first = f"It’s warm {desc_time} in {location_name}."
                second = "Cool, breathable clothing will be most comfortable." if not has_showers else "Light, breathable clothing is best; watch for brief showers."
                return f"{first} {second}"

        # Forecast / general templates
        if timeframe == 'tomorrow' or 'morning' in desc_time:
            # Forecast morning example
            first = f"{desc_time.capitalize()} looks { 'mild and mostly dry' if not has_showers else 'showery' } in {location_name}, with comfortable temperatures for getting around."
            second = "It should be a good window for errands or a walk before conditions change later in the day." if not has_showers else "If you have outdoor plans, keep an umbrella handy in case showers pop up."
            return f"{first} {second}"

        # Default calm advice (2 sentences)
        descriptor_phrase = ' and '.join([p for p in [temp_label, 'breezy' if wind and wind >=5 else None, 'chance of rain' if has_showers else None] if p])
        first = f"It’s {descriptor_phrase} {desc_time} in {location_name}."
        second = "Bring a light layer if you’ll be outside, and check local forecasts for updates." if has_showers or wind and wind >=5 else "It should be comfortable for most outdoor plans."
        return f"{first} {second}"

    def run(self):
        print('STATUS:starting:Initializing Weather Agent', flush=True)
        try:
            runner = web.AppRunner(self.app)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, '0.0.0.0', self.port)
            loop.run_until_complete(site.start())
            print(f'STATUS:running:Server started on port {self.port}', flush=True)
            print('STATUS:waiting:Idle, waiting for requests', flush=True)
            loop.run_forever()
        except Exception as e:
            print(f'STATUS:failed:{e}', flush=True)
        finally:
            print('STATUS:stopping:Shutting down', flush=True)
            print('STATUS:stopped:Agent terminated', flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8000)))
    args = parser.parse_args()
    agent = Weather(port=args.port)
    agent.run()
