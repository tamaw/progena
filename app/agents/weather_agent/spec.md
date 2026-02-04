# Build Spec: weather_agent

> Task: lets build the weather agent again

**Build Description:** An A2A agent that retrieves current weather conditions and forecasts for a given location and reports them to the user.

**Project Name:** weather_agent
> Generated at: 2026-02-03T11:13:51.362277+00:00

---

```markdown
# Agent Structure Specification — weather_agent

This document defines the COMPLETE build specification for the **weather_agent**, an A2A-compliant HTTP agent that retrieves **current weather conditions and weather forecasts** for a user-specified location and reports them in a clear, structured format.

---

## Project Overview

- **Project Name:** weather_agent  
- **Agent Name:** weather_agent  
- **Agent Class:** WeatherAgent  
- **Protocol:** A2A (HTTP-based)  
- **Purpose:**  
  Retrieve current weather conditions and short-term forecasts for a given location using an external weather API and return a human-readable weather report.

---

## Directory Structure (CRITICAL — MUST FOLLOW EXACTLY)

**DO NOT create files directly in `app/agents/`.**

All files MUST exist under the subdirectory:

```
app/agents/weather_agent/
```

### Required Files

```
app/agents/weather_agent/__init__.py
app/agents/weather_agent/agent.py
app/agents/weather_agent/README.md
```

❌ WRONG:
```
app/agents/weather_agent.py
app/agents/weather_agent_agent.py
```

✅ CORRECT:
```
app/agents/weather_agent/agent.py
```

---

## Agent Class Requirements

The agent class MUST be named **WeatherAgent** and MUST define the following class attributes:

```python
AGENT_ID = "weather_agent"
AGENT_NAME = "Weather Agent"
DESCRIPTION = "Retrieves current weather conditions and weather forecasts for a specified location."
CAPABILITIES = [
    "current_weather",
    "weather_forecast",
    "location_based_weather"
]
```

---

## Transport Type

✅ **HTTP Agent (Required)**  
The weather agent MUST be implemented as an HTTP server using `aiohttp` and MUST expose the required A2A endpoints.

---

## Required HTTP Endpoints

### 1. GET `/.well-known/agent.json` — Agent Card

Returns metadata describing the agent.

#### Response (JSON)
```json
{
  "name": "Weather Agent",
  "description": "Retrieves current weather conditions and weather forecasts for a specified location.",
  "version": "0.1.0",
  "protocol": "a2a",
  "capabilities": [
    "current_weather",
    "weather_forecast",
    "location_based_weather"
  ],
  "endpoint": "http://localhost:{port}"
}
```

---

### 2. GET `/health` — Health Check

Used by the agent manager to verify liveness.

#### Response (JSON)
```json
{
  "status": "healthy",
  "name": "Weather Agent",
  "version": "0.1.0",
  "uptime_seconds": 452.32,
  "timestamp": "2026-01-01T12:00:00Z"
}
```

---

### 3. POST `/` — Main Request Handler

Primary interface for retrieving weather data.

#### Request Format
```json
{
  "request": "weather in London tomorrow",
  "context": {},
  "id": "optional-request-id"
}
```

- `request` (string, REQUIRED): Natural language weather request
- `context` (object, OPTIONAL): May include structured hints (location, dates)
- `id` (string, OPTIONAL): Request identifier

---

## Supported Request Types

The agent MUST support the following request intents:

### 1. Current Weather

Examples:
- `"weather in Paris"`
- `"current weather for Tokyo"`
- `"what's the weather in New York right now"`

### 2. Forecast Weather

Examples:
- `"weather in Berlin tomorrow"`
- `"3 day forecast for Madrid"`
- `"weekly forecast for San Francisco"`

---

## Location Resolution Rules

- If a city name is provided, use it directly.
- If city + country is provided, prioritize exact match.
- If no location is provided:
  - Attempt to infer from `context.location`
  - Otherwise return an error response.

---

## External Weather API Requirements

The agent MUST:
- Use a public weather API (e.g., OpenWeatherMap, WeatherAPI, or equivalent)
- Support:
  - Current temperature
  - Weather condition description
  - Humidity
  - Wind speed
  - Forecast highs/lows (when applicable)
- Store API key in an environment variable:
  ```
  WEATHER_API_KEY
  ```

---

## Weather Data Normalization Rules

- Temperature: **Celsius**
- Wind speed: **km/h**
- Timezone: Local to the requested location
- Dates: ISO 8601 internally, human-readable in responses

---

## Response Format (POST `/`)

### Success Response (REQUIRED FORMAT)

```json
{
  "success": true,
  "response": "Weather report text",
  "error": null,
  "metadata": {
    "location": "London, UK",
    "type": "current | forecast",
    "source": "openweathermap",
    "units": "metric"
  }
}
```

---

## EXACT Response Text Requirements

### Current Weather Response (EXACT FORMAT)

```
Current weather in {Location}:

Temperature: {temp}°C
Condition: {condition}
Humidity: {humidity}%
Wind: {wind_speed} km/h
```

#### Example
```
Current weather in London, UK:

Temperature: 18°C
Condition: Partly cloudy
Humidity: 62%
Wind: 14 km/h
```

---

### Forecast Weather Response (EXACT FORMAT)

```
Weather forecast for {Location}:

{Day 1}:
  High: {high}°C
  Low: {low}°C
  Condition: {condition}

{Day 2}:
  High: {high}°C
  Low: {low}°C
  Condition: {condition}
```

#### Example
```
Weather forecast for Berlin, Germany:

Tomorrow:
  High: 21°C
  Low: 13°C
  Condition: Light rain

Day after tomorrow:
  High: 19°C
  Low: 11°C
  Condition: Cloudy
```

---

## Error Handling Rules

### Missing Location

If no location can be determined:

```json
{
  "success": false,
  "response": null,
  "error": "No location specified. Please provide a city or location.",
  "metadata": {}
}
```

### Unknown Location / API Failure

```json
{
  "success": false,
  "response": null,
  "error": "Unable to retrieve weather data for the specified location.",
  "metadata": {
    "location": "Atlantis"
  }
}
```

---

## Status Protocol (MANDATORY)

The agent MUST emit the following status lines to stdout:

```
STATUS:starting:Initializing Weather Agent
STATUS:running:Server started on port {port}
STATUS:executing:Processing weather request
STATUS:waiting:Idle, waiting for requests
STATUS:failed:{error_message}
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

---

## WeatherAgent.process() Behavior

### Method Signature
```python
async def process(self, request: str, context: dict) -> str
```

### Behavior Rules

1. Parse request intent:
   - Detect **current** vs **forecast**
2. Extract location:
   - From request text
   - Or `context["location"]`
3. Call weather API
4. Normalize data
5. Format response EXACTLY as specified
6. Return formatted weather report string

---

## README.md Requirements

The README MUST include:
- Agent purpose
- Supported request examples
- Environment variables
- How to run the agent
- Example curl request

---

## Non-Functional Requirements

- No base agent inheritance
- Self-contained implementation
- Async I/O only
- Deterministic output formatting
- Human-readable responses only (no raw JSON weather dumps)

---

✅ **This specification is complete and sufficient to implement the weather_agent exactly as required.**


## Iteration 1 Updates
- **primary**: Below are **three distinct, structured approaches** to reach the ideal state of rebuilding the **weather_agent**, each explicitly addressing the previous failure (“task not completed successfully”) wh
- **alternative_2**: Alternative approach with different trade-offs

### Gaps to Address
- Criterion 'Task completed successfully' not met (score: 0.00)

### Lessons Learned
#### Iteration 0
- **What failed:** task_completed_successfully
- **Adjustments:** Address gap: Criterion 'Task completed successfully' not met (score: 0.00)