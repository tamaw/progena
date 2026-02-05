# Build Spec: weather_agent

> Task: build weather agent

**Build Description:** An A2A conversational agent that retrieves real-time weather data and forecasts and explains current conditions and upcoming weather in a clear, user-friendly way.

**Project Name:** weather_agent
> Generated at: 2026-02-04T13:51:37.783078+00:00

---

```markdown
# Agent Structure Specification — weather_agent

## Project Overview

**Project Name:** weather_agent  
**Agent Name:** weather_agent  
**Agent Class:** WeatherAgent  

**Purpose:**  
An A2A-compatible conversational HTTP agent that retrieves **real-time weather conditions and short-term forecasts** for a given location and explains them in **clear, plain-language summaries** suitable for non-technical users.

The agent is designed to:
- Accept natural-language weather questions
- Resolve locations and timeframes from user input
- Retrieve live weather data from an external API
- Explain conditions, temperatures, precipitation, wind, and hazards
- Provide concise forecasts (today, tomorrow, next 7 days)
- Handle errors and ambiguities gracefully

---

## Directory Structure (CRITICAL - MUST FOLLOW EXACTLY)

**DO NOT create files directly in `app/agents/`. ALL agent files MUST go in a subdirectory.**

Create the agent in the subdirectory:

```
app/agents/weather_agent/
```

With these EXACT files:

- `app/agents/weather_agent/__init__.py`  
  - Exports the `WeatherAgent` class

- `app/agents/weather_agent/agent.py`  
  - Main agent implementation (REQUIRED)

- `app/agents/weather_agent/README.md`  
  - Human-readable documentation for usage and behavior

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

## Agent Identity & Discovery

### Required Class Attributes

The `WeatherAgent` class MUST define:

```python
AGENT_ID = "weather_agent"
AGENT_NAME = "Weather Agent"
DESCRIPTION = "Provides real-time weather conditions and forecasts in clear, user-friendly language"
CAPABILITIES = [
    "current_weather",
    "weather_forecast",
    "location_weather_lookup",
    "weather_explanation",
    "weather_alerts_summary"
]
```

---

## Transport Type

✅ **HTTP Agent (A2A-compliant)**  
The weather agent MUST be implemented as an HTTP server using `aiohttp`.

---

## HTTP Agent Requirements (CRITICAL)

The agent MUST implement **all three required endpoints** and follow the response schemas exactly.

---

## Endpoint 1: Agent Card

### `GET /.well-known/agent.json`

**Purpose:**  
Allows agent discovery via the A2A protocol.

### Response Format (EXACT):

```json
{
    "name": "Weather Agent",
    "description": "Provides real-time weather conditions and forecasts in clear, user-friendly language",
    "version": "0.1.0",
    "protocol": "a2a",
    "capabilities": [
        "current_weather",
        "weather_forecast",
        "location_weather_lookup",
        "weather_explanation",
        "weather_alerts_summary"
    ],
    "endpoint": "http://localhost:{port}"
}
```

---

## Endpoint 2: Health Check

### `GET /health`

**Purpose:**  
Allows agent manager to verify agent health and uptime.

### Response Format (EXACT):

```json
{
    "status": "healthy",
    "name": "Weather Agent",
    "version": "0.1.0",
    "uptime_seconds": 123.45,
    "timestamp": "2024-01-01T00:00:00Z"
}
```

---

## Endpoint 3: Main Request Handler

### `POST /`

### Request Format (EXACT):

```json
{
    "request": "What is the weather in Paris tomorrow?",
    "context": {
        "units": "metric",
        "language": "en"
    },
    "id": "optional-request-id"
}
```

### Required Request Handling Behavior

The agent MUST:

1. Emit status:
   ```
   STATUS:executing:Processing request
   ```

2. Parse the `request` field as natural language
3. Extract or infer:
   - **Location** (city, region, country, or coordinates)
   - **Timeframe** (now, today, tomorrow, specific date, next 7 days)
4. If location is missing or ambiguous:
   - Ask a **clarifying question** in the response
5. Fetch weather data from a real-time weather API (e.g., OpenWeatherMap, WeatherAPI)
6. Convert raw data into **plain-language explanations**
7. Emit:
   ```
   STATUS:waiting:Idle, waiting for requests
   ```

---

## Response Format (EXACT)

```json
{
    "success": true,
    "response": "Human-readable weather explanation",
    "error": null,
    "metadata": {
        "location": "Paris, FR",
        "timeframe": "tomorrow",
        "units": "metric",
        "data_source": "external_weather_api"
    }
}
```

---

## Weather Explanation Rules (CRITICAL)

All weather responses MUST:

- Use **non-technical language**
- Avoid API-specific jargon
- Use full sentences
- Be concise but informative (3–6 sentences)
- Include temperatures, conditions, and notable risks

---

## Exact Response Behaviors

### 1. Current Weather Query

**Input:**
```json
{
    "request": "What's the weather like in New York right now?",
    "context": {}
}
```

**Response (Example Structure):**
```json
{
    "success": true,
    "response": "It is currently 22°C in New York City with partly cloudy skies. The air feels comfortable, and there is a light breeze coming from the west. No rain is expected in the next few hours.",
    "error": null,
    "metadata": {
        "location": "New York City, US",
        "timeframe": "current",
        "units": "metric"
    }
}
```

---

### 2. Tomorrow Forecast

**Input:**
```json
{
    "request": "Will it rain in London tomorrow?",
    "context": {}
}
```

**Required Behavior:**
- Explicitly answer the rain question
- Include temperature range
- Mention confidence if precipitation is likely

**Response MUST include:**
- Yes/no on rain
- High/low temperature
- General conditions

---

### 3. Multi-Day Forecast

**Input:**
```json
{
    "request": "Give me a 7 day forecast for Tokyo",
    "context": {}
}
```

**Response Rules:**
- Summarize trends, not hourly detail
- Mention temperature trend and major weather events
- No more than 8 sentences total

---

### 4. Missing Location

**Input:**
```json
{
    "request": "What's the weather like today?",
    "context": {}
}
```

**Response MUST be:**
```json
{
    "success": true,
    "response": "I can help with that — could you please tell me the city or location you want the weather for?",
    "error": null,
    "metadata": {
        "reason": "missing_location"
    }
}
```

---

### 5. Invalid or Unsupported Request

**Input:**
```json
{
    "request": "Tell me a joke",
    "context": {}
}
```

**Response MUST be:**
```json
{
    "success": false,
    "response": null,
    "error": "Unsupported request. I can provide current weather and forecasts for specific locations.",
    "metadata": {}
}
```

---

## Error Handling Rules

- API failures → return `success: false` with a clear explanation
- Timeouts → explain temporary unavailability
- Never expose API keys or stack traces

---

## Status Protocol (MANDATORY)

The agent MUST emit these lines to stdout:

```
STATUS:starting:Initializing Weather Agent
STATUS:running:Server started on port {port}
STATUS:executing:Processing request
STATUS:waiting:Idle, waiting for requests
STATUS:failed:{error message}
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

---

## README.md Requirements

The README MUST include:
- Agent purpose
- Example curl request
- Example response
- Supported query types
- Environment variables required (e.g., WEATHER_API_KEY)

---

## Summary

The `weather_agent` is a **self-contained A2A HTTP agent** that:
- Understands natural-language weather questions
- Retrieves real-time and forecast weather data
- Explains weather clearly and safely
- Strictly adheres to the A2A protocol and response formats
```