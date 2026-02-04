Weather Agent

Agent purpose

The Weather Agent provides real-time weather conditions, hourly (next 12 hours) and daily (next 3 days) forecasts, and plain-language explanations of weather terms.

Supported request examples

- Current weather: {"request": "What's the weather in London?", "context": {"units": "metric"}}
- Hourly forecast: {"request": "Will it rain later today in Seattle?", "context": {"location": "Seattle, US", "units": "imperial"}}
- Daily forecast: {"request": "3-day forecast for Rome", "context": {"units": "metric"}}
- Explanation: {"request": "What does humidity mean?", "context": {"language": "en"}}

Required environment variables

- WEATHER_API_KEY — API key for OpenWeatherMap

How to run the agent:

```bash
python agent.py --port 8000
```

Example curl request

```bash
curl -X POST "http://localhost:8000/" -H "Content-Type: application/json" -d '{"request":"What's the weather in London?","context":{"units":"metric"}}'
```

Example response

```json
{
  "success": true,
  "response": "Current weather in London, UK:\n• Temperature: 18°C (feels like 17°C)\n• Conditions: Light rain\n• Wind: 12 km/h\n• Humidity: 72%",
  "error": null,
  "metadata": {
    "location": "London, UK",
    "coordinates": {"lat": 51.51, "lon": -0.13},
    "units": "metric",
    "data_source": "OpenWeatherMap",
    "request_id": null
  }
}
```
