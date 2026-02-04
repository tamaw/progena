# Build Spec: weather_agent

> Task: keep building the best weather agent

**Build Description:** An advanced A2A weather agent that retrieves current conditions and forecasts, presents them in a clear and engaging format, and is designed to be reliable, fast, and user-friendly.

**Project Name:** weather_agent
> Generated at: 2026-02-03T21:24:41.232026+00:00

---

```markdown
# Agent Structure Specification — weather_agent

## Overview

**Project Name:** weather_agent  
**Agent Name:** weather_agent  
**Agent Class:** WeatherAgent  

The **weather_agent** is an advanced A2A-compliant HTTP agent that retrieves **current weather conditions**, **short-term forecasts**, and **multi-day forecasts** for a specified location. It is optimized for **speed, reliability, clarity, and user experience**, producing concise, human-readable weather summaries while also supporting structured metadata for downstream agents.

The agent is designed to:
- Parse natural-language weather queries
- Resolve locations reliably (city, region, country; optional lat/lon)
- Fetch real-time weather and forecast data from an external weather API
- Normalize and format results consistently
- Handle errors gracefully with actionable messages

---

## Directory Structure (CRITICAL - MUST FOLLOW EXACTLY)

**DO NOT create files directly in `app/agents/`. ALL agent files MUST go in a subdirectory.**

Create the agent in the subdirectory:

```
app/agents/weather_agent/
```

With these EXACT paths:

- `app/agents/weather_agent/__init__.py`  
  - Exports the `WeatherAgent` class
- `app/agents/weather_agent/agent.py`  
  - Main agent implementation (REQUIRED)
- `app/agents/weather_agent/README.md`  
  - End-user and developer documentation

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

The `WeatherAgent` class MUST define the following class attributes:

```python
AGENT_ID = "weather_agent"
AGENT_NAME = "Weather Agent"
DESCRIPTION = "Provides current weather conditions and forecasts for any location"
CAPABILITIES = [
    "current_weather",
    "hourly_forecast",
    "daily_forecast",
    "location_weather_lookup",
    "weather_summary"
]
```

---

## HTTP Agent Requirements (A2A COMPLIANT)

The **weather_agent** MUST be implemented as an **HTTP agent** using `aiohttp`.

### Required Endpoints

---

### 1. GET `/.well-known/agent.json` — Agent Card

**Purpose:**  
Expose agent identity and capabilities for A2A discovery.

**Response (EXACT JSON SHAPE):**
```json
{
  "name": "Weather Agent",
  "description": "Provides current weather conditions and forecasts for any location",
  "version": "0.1.0",
  "protocol": "a2a",
  "capabilities": [
    "current_weather",
    "hourly_forecast",
    "daily_forecast",
    "location_weather_lookup",
    "weather_summary"
  ],
  "endpoint": "http://localhost:{port}"
}
```

---

### 2. GET `/health` — Health Check

**Purpose:**  
Allow agent manager to verify runtime health and uptime.

**Response (EXACT JSON SHAPE):**
```json
{
  "status": "healthy",
  "name": "Weather Agent",
  "version": "0.1.0",
  "uptime_seconds": 123.45,
  "timestamp": "2024-01-01T00:00:00Z"
}
```

**Behavioral Requirements:**
- `uptime_seconds` MUST be calculated from agent startup time
- `timestamp` MUST be ISO-8601 UTC

---

### 3. POST `/` — Main Request Handler

**Purpose:**  
Accept weather-related requests and return formatted results.

#### Request Format

```json
{
  "request": "the main request content",
  "context": {},
  "id": "optional-request-id"
}
```

- `request` (string): Natural-language weather query
- `context` (object): Optional structured hints (location, units, dates)
- `id` (string, optional): Passed through to metadata if provided

---

#### Response Format (SUCCESS)

```json
{
  "success": true,
  "response": "human-readable weather output",
  "error": null,
  "metadata": {
    "location": "Resolved location name",
    "latitude": 0.0,
    "longitude": 0.0,
    "units": "metric|imperial",
    "data_source": "external_weather_api_name",
    "request_id": "optional-request-id"
  }
}
```

---

#### Response Format (ERROR)

```json
{
  "success": false,
  "response": null,
  "error": "Clear, user-facing error message",
  "metadata": {
    "request_id": "optional-request-id"
  }
}
```

---

## Supported Request Types & EXACT Behavior

### 1. Current Weather Request

**Example Inputs:**
- `"What's the weather in Paris right now?"`
- `"current weather Tokyo"`
- `"How hot is it in New York?"`

**Expected Agent Behavior:**
- Resolve location to latitude/longitude
- Retrieve **current conditions only**
- Ignore forecast data
- Use default units:
  - Metric (°C, km/h) unless context specifies `"units": "imperial"`

**EXACT Response Text Format:**

```
🌤 Current Weather for Paris, France
Temperature: 18°C (feels like 17°C)
Condition: Partly cloudy
Humidity: 62%
Wind: 10 km/h NW
```

---

### 2. Hourly Forecast Request (Next 12–24 Hours)

**Example Inputs:**
- `"Hourly forecast for Berlin"`
- `"What will the weather be like later today in Chicago?"`

**Expected Agent Behavior:**
- Return up to **12 hours** of forecast
- Use local time for the location
- Summarize concisely

**EXACT Response Text Format:**

```
⏱ Hourly Forecast for Berlin, Germany (Next 12 Hours)
14:00 — 17°C, Cloudy
15:00 — 18°C, Cloudy
16:00 — 18°C, Light rain
17:00 — 17°C, Light rain
```

---

### 3. Daily Forecast Request (3–7 Days)

**Example Inputs:**
- `"7-day forecast for San Francisco"`
- `"Weather this week in Madrid"`

**Expected Agent Behavior:**
- Default to **5-day forecast** unless otherwise specified
- Include high/low temperatures and conditions

**EXACT Response Text Format:**

```
📅 5-Day Forecast for Madrid, Spain

Mon — ☀️ Sunny, High: 26°C / Low: 14°C
Tue — 🌤 Partly cloudy, High: 24°C / Low: 13°C
Wed — 🌦 Showers, High: 22°C / Low: 12°C
Thu — ⛅ Mostly cloudy, High: 23°C / Low: 13°C
Fri — ☀️ Sunny, High: 25°C / Low: 14°C
```

---

### 4. Ambiguous or Missing Location

**Example Input:**
- `"What's the weather like?"`

**Expected Behavior:**
- Do NOT guess a location
- Prompt the user clearly

**EXACT Response:**
```json
{
  "success": false,
  "response": null,
  "error": "Please specify a location (city, region, or coordinates) to get weather information.",
  "metadata": {}
}
```

---

### 5. Unsupported or Non-Weather Requests

**Example Input:**
- `"Tell me a joke"`

**Expected Behavior:**
- Politely refuse
- State agent scope

**EXACT Response Text:**
```
I can help with current weather conditions and forecasts. Please ask about the weather for a specific location.
```

---

## invoke / process Logic Specification

The `process(request: str, context: dict) -> str` method MUST:

1. Detect intent:
   - current weather
   - hourly forecast
   - daily forecast
2. Resolve location using:
   - `context.location` if provided
   - Otherwise extract from natural language
3. Call external weather API with timeout ≤ 5 seconds
4. Normalize units and conditions
5. Format output EXACTLY as specified above
6. Never return raw API JSON in `response`

---

## Status Protocol (MANDATORY)

The agent MUST emit status lines to stdout:

```
STATUS:starting:Initializing Weather Agent
STATUS:running:Server started on port {port}
STATUS:executing:Processing weather request
STATUS:waiting:Idle, waiting for requests
STATUS:failed:{error message}
STATUS:stopping:Shutting down Weather Agent
STATUS:stopped:Agent terminated
```

---

## README.md Requirements

The `README.md` MUST include:
- Agent purpose
- Supported request examples
- API dependencies (without embedding secrets)
- Example curl request
- Example response

---

## Reliability & Performance Requirements

- API timeout: **≤ 5 seconds**
- Graceful handling of:
  - API downtime
  - Invalid locations
  - Rate limiting
- No uncaught exceptions should crash the agent
- All errors must return valid A2A JSON responses

---

✅ This specification is COMPLETE and MUST be followed EXACTLY to implement the **weather_agent**.
```