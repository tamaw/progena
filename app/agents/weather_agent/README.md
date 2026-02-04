# Weather Agent

Purpose
-------

The Weather Agent is an A2A HTTP agent that retrieves current weather conditions and short-term forecasts for a specified location and returns a human-readable weather report.

Supported request examples
--------------------------

- "weather in Paris"
- "current weather for Tokyo"
- "what's the weather in New York right now"
- "weather in Berlin tomorrow"
- "3 day forecast for Madrid"

Environment variables
---------------------

- WEATHER_API_KEY: API key for OpenWeatherMap (required for full functionality)
- PORT: optional, HTTP server port (default 8080)

How to run
----------

1. Install dependencies: `pip install aiohttp`
2. Set `WEATHER_API_KEY` environment variable to your OpenWeatherMap API key.
3. Run the agent: `python -m app.agents.weather_agent.agent`

Example curl request
--------------------

Current weather:

curl -X POST http://localhost:8080/ -H "Content-Type: application/json" -d '{"request": "weather in London"}'

Forecast:

curl -X POST http://localhost:8080/ -H "Content-Type: application/json" -d '{"request": "weather in London tomorrow"}'
