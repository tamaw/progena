# Weather Agent

Purpose

The Weather Agent provides calm, human-friendly weather explanations and practical advice focused on comfort, clothing, and safety. It translates forecasts into short, reassuring guidance rather than raw data.

Example requests and responses

- Request: `{ "request": "Do I need a jacket in Berlin?", "context": { "location": "Berlin", "timeframe": "today", "intent": "clothing" } }`
- Response: `It’s on the cool side today in Berlin, and the breeze makes it feel a bit colder than it looks. A light jacket will keep you comfortable, especially if you’ll be outside for a while.`

Supported timeframes and intents

- Timeframes: now, today, tomorrow, next_7_days
- Intents: travel, clothing, safety, outdoor_plans

Setup

1. Obtain an OpenWeather API key and set it in the environment:

```powershell
$env:OPENWEATHER_API_KEY = "your_api_key_here"
```

2. Run the agent and specify a port (default 8000):

```powershell
python -m app.agents.weather.agent --port 8000
```

Port

The agent listens on the provided --port (or PORT environment variable). Endpoints:

- `GET /.well-known/agent.json` - agent card
- `GET /health` - health check
- `POST /` - main request handler (see examples above)

