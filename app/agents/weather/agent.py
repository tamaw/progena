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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return web.json_response(payload)

    async def handle_request(self, request):
        print('STATUS:executing:Processing request')
        try:
            data = await request.json()
        except Exception:
            print('STATUS:failed:Invalid JSON')
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

        if not user_request and not location:
            print('STATUS:waiting:Idle, waiting for requests')
            return web.json_response({
                "success": True,
                "response": "I can help with that — which city or location are you asking about?",
                "error": None,
                "metadata": {},
            })

        if not location:
            # try to infer location from request naive (not implemented). ask clarification.
            print('STATUS:waiting:Idle, waiting for requests')
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
            print(f'STATUS:failed:{e}')
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
            print(f'STATUS:failed:{e}')
            return web.json_response({
                "success": False,
                "response": "I'm having trouble reaching the weather service right now. Please try again in a moment.",
                "error": str(e),
                "metadata": {"location": resolved_name},
            }, status=502)

        # Interpret and craft advice
        try:
            advice = self.generate_advice(weather, timeframe, intent, resolved_name)
        except Exception as e:
            print(f'STATUS:failed:{e}')
            return web.json_response({
                "success": False,
                "response": "Sorry, something went wrong while preparing advice.",
                "error": str(e),
                "metadata": {"location": resolved_name},
            }, status=500)

        print('STATUS:waiting:Idle, waiting for requests')
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
        if not self.api_key:
            raise ValueError('Missing OPENWEATHER_API_KEY')

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
            raise RuntimeError('Missing OPENWEATHER_API_KEY')
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

    def generate_advice(self, weather, timeframe, intent, location_name):
        # Determine main descriptors
        current = weather.get('current', {})
        hourly = weather.get('hourly', [])
        daily = weather.get('daily', [])
        alerts = weather.get('alerts', [])

        # Choose sample based on timeframe
        now_dt = datetime.now(timezone.utc)
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

        feels_like = sample.get('feels_like')
        if isinstance(feels_like, dict):
            feels = feels_like.get('day')
        else:
            feels = feels_like or current.get('feels_like')

        wind = sample.get('wind_speed') or current.get('wind_speed', 0)
        pop = sample.get('pop') if sample.get('pop') is not None else (hourly[0].get('pop') if hourly else 0)

        # Basic qualitative
        descriptors = []
        if temp_val is not None:
            if temp_val <= 5:
                descriptors.append('very cold')
            elif temp_val <= 15:
                descriptors.append('cool')
            elif temp_val <= 25:
                descriptors.append('mild')
            else:
                descriptors.append('warm')

        if wind and wind >= 10:
            descriptors.append('breezy to windy')
        elif wind and wind >= 5:
            descriptors.append('breezy')

        if pop and pop >= 0.3:
            descriptors.append('chance of rain')

        # Alerts override tone for safety
        alert_text = ''
        if alerts:
            alert_text = ' There are weather alerts in effect; please follow local guidance.'

        # Clothing advice simplified
        clothing = ''
        if intent == 'clothing':
            if temp_val is None:
                clothing = "Bring layers to stay comfortable."
            else:
                if temp_val <= 10:
                    clothing = "A warm jacket and layers will keep you comfortable."
                elif temp_val <= 18:
                    clothing = "A light jacket or sweater should be fine."
                elif temp_val <= 25:
                    clothing = "Light clothing will be comfortable."
                else:
                    clothing = "Cool, breathable clothing is best."
        else:
            # Provide a brief neutral clothing hint when relevant
            if temp_val is not None:
                if temp_val <= 10:
                    clothing = "It's cool — layers are a good idea."
                elif temp_val <= 18:
                    clothing = "You might want a light jacket."
                elif temp_val <= 25:
                    clothing = "Light clothing should be fine."

        # Safety advice
        safety = ''
        if alerts:
            safety = 'If you have outdoor plans, consider postponing or staying close to shelter.'
        elif pop and pop >= 0.6:
            safety = 'Expect steady rain; keep a waterproof layer handy.'
        elif wind and wind >= 15:
            safety = 'High winds are possible — secure loose items and be cautious if outside.'

        # Compose short paragraph (2-4 sentences)
        descriptor_str = ', '.join(descriptors) if descriptors else 'with no significant hazards'
        paragraph = f"{location_name} looks {descriptor_str} {desc_time}. {clothing}"
        if safety:
            paragraph += f" {safety}"
        if alert_text:
            paragraph += alert_text

        # Keep it concise and calm
        return paragraph.strip()

    def run(self):
        print('STATUS:starting:Initializing Weather Agent')
        try:
            runner = web.AppRunner(self.app)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, '0.0.0.0', self.port)
            loop.run_until_complete(site.start())
            print(f'STATUS:running:Server started on port {self.port}')
            print('STATUS:waiting:Idle, waiting for requests')
            loop.run_forever()
        except Exception as e:
            print(f'STATUS:failed:{e}', file=sys.stderr)
        finally:
            print('STATUS:stopping:Shutting down')
            print('STATUS:stopped:Agent terminated')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8000)))
    args = parser.parse_args()
    agent = Weather(port=args.port)
    agent.run()
