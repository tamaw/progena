# Weather Agent

Purpose:

A conversational A2A HTTP agent that retrieves real-time weather conditions and short-term forecasts and explains them in clear, user-friendly language.

Supported question examples:

- What’s the weather in Tokyo right now?
- Will it rain in London tomorrow?
- What’s the weather looking like in New York this week?
- What’s the weather like today?

Environment variables required:

- WEATHER_API_KEY - API key for OpenWeatherMap (recommended)

How to run the agent:

```bash
python agent.py --port 8080
```

Notes:

- The agent uses the OpenWeatherMap Geocoding and One Call APIs by default. If WEATHER_API_KEY is not set or API calls fail, the agent will return a friendly error message.
- Units default to metric (°C, km/h). Set context.units to "imperial" or include 'F' or 'fahrenheit' in the request to request imperial units.
