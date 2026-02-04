# Weather Agent

Purpose
-------

An advanced A2A weather agent that retrieves current conditions, hourly forecasts, and multi-day forecasts, and returns clear, engaging, location-aware summaries suitable for agent-to-agent calls and UIs.

Supported queries (examples)
----------------------------

- "What's the weather in San Francisco right now?"
- "Forecast for Berlin today"
- "5 day forecast Tokyo"
- "Hourly weather NYC"
- "Summarize tomorrow's weather in Rome"

Endpoint summary
----------------

- GET /.well-known/agent.json — discovery card with metadata
- GET /health — health check with uptime in seconds (UTC timestamp)
- POST / — main request handler (JSON)

Example curl request
--------------------

curl -X POST http://localhost:8080/ -H 'Content-Type: application/json' -d '{"request": "What\'s the weather in San Francisco today?", "context": {"units": "metric", "forecast_days": 3}}'

Example response
----------------

{
  "success": true,
  "response": "🌤️ Current Weather for San Francisco, CA\n\nTemperature: 18°C (feels like 17°C)\nCondition: Partly Cloudy\nHumidity: 72%\nWind: 12 km/h\nPrecipitation Chance: 10%",
  "error": null,
  "metadata": {
    "location": "San Francisco, CA",
    "units": "metric",
    "forecast_days": 3,
    "data_source": "simulated"
  }
}

Environment variables
---------------------

- AGENT_PORT — optional port for the HTTP server (default: 8080)

Notes
-----

This agent simulates weather data deterministically per location and date to ensure consistent responses and low-latency behavior without an external API. It logs status lines to stdout in the required STATUS:<state>:<message> format.
