Weather Lookup Agent

This HTTP agent provides current weather information using the OpenWeather API.

Usage:
- Set environment variable OPENWEATHER_API_KEY with your API key.
- Run the agent: python -m app.agents.weather_lookup.agent --port 8080

Endpoints:
- GET /.well-known/agent.json  -> Agent discovery card
- GET /health                 -> Health status
- POST /                      -> Main handler; JSON body: {"request": "location", "context": {}}

Capabilities:
- weather_lookup
- current_weather
- location_based_query

Notes:
- The agent returns normalized JSON including temperature, humidity, description, wind, sunrise/sunset and raw API payload.