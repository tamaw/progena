# Build Spec: weather_agent

> Task: build weather agent

**Build Description:** An A2A conversational agent that retrieves real-time weather data, provides current conditions and short-term forecasts, and explains weather information in a clear, user-friendly way.

**Project Name:** weather_agent
> Generated at: 2026-02-04T13:12:15.430732+00:00

---

```markdown
# Agent Structure Specification — weather_agent

This document defines the **complete build specification** for the `weather_agent` A2A conversational agent.  
All requirements in this document are **mandatory** unless explicitly marked optional.

---

## Project Overview

- **Project Name:** weather_agent  
- **Agent Name:** weather_agent  
- **Agent Class:** `WeatherAgent`  
- **Protocol:** A2A (Agent-to-Agent)  
- **Transport:** HTTP (aiohttp-based server)

### Purpose

`weather_agent` is an A2A conversational agent that:
- Retrieves **real-time weather data**
- Provides **current weather conditions**
- Provides **short-term forecasts (up to 7 days, default 3 days)**
- Explains weather information in **clear, non-technical, user-friendly language**

The agent must be usable both by:
- Humans (natural language queries)
- Other agents (structured requests)

---

## Directory Structure (CRITICAL — MUST FOLLOW EXACTLY)

**DO NOT create files directly in `app/agents/`.**  
ALL agent files MUST be placed in a subdirectory.

✅ **Correct structure:**

```
app/
└── agents/
    └── weather_agent/
        ├── __init__.py
        ├── agent.py
        └── README.md
```

❌ **Incorrect examples (DO NOT USE):**
- `app/agents/weather_agent.py`
- `app/agents/weather_agent_agent.py`

---

## Agent Metadata

### Class Attributes (MANDATORY)

The `WeatherAgent` class MUST define the following class attributes:

```python
AGENT_ID = "weather_agent"
AGENT_NAME = "Weather Agent"
DESCRIPTION = "Provides real-time weather conditions, short-term forecasts, and clear explanations of weather data."
CAPABILITIES = [
    "current_weather",
    "weather_forecast",
    "weather_explanation",
    "location_resolution"
]
```

---

## HTTP Agent Requirements (CRITICAL)

The agent MUST be implemented as an **HTTP server** using `aiohttp`.

There is **no shared base class** — the agent must be fully self-contained.

---

## Required HTTP Endpoints

### 1. GET `/.well-known/agent.json` — Agent Card

**Purpose:**  
Used by the A2A agent manager for discovery.

**Response JSON (EXACT FIELDS REQUIRED):**
```json
{
  "name": "Weather Agent",
  "description": "Provides real-time weather conditions, short-term forecasts, and clear explanations of weather data.",
  "version": "0.1.0",
  "protocol": "a2a",
  "capabilities": [
    "current_weather",
    "weather_forecast",
    "weather_explanation",
    "location_resolution"
  ],
  "endpoint": "http://localhost:{port}"
}
```

---

### 2. GET `/health` — Health Check

**Purpose:**  
Used for monitoring and orchestration.

**Response JSON:**
```json
{
  "status": "healthy",
  "name": "Weather Agent",
  "version": "0.1.0",
  "uptime_seconds": 123.45,
  "timestamp": "2024-01-01T00:00:00+00:00"
}
```

- `uptime_seconds` MUST be calculated from agent startup time.
- `timestamp` MUST be UTC ISO-8601 format.

---

### 3. POST `/` — Main Request Handler

#### Request Format

```json
{
  "request": "What is the weather in Paris today?",
  "context": {},
  "id": "optional-request-id"
}
```

- `request` (string): Natural language user or agent request.
- `context` (object): Optional structured metadata (location hints, units, language).
- `id` (string, optional): Request identifier.

---

#### Response Format (SUCCESS)

```json
{
  "success": true,
  "response": "The current weather in Paris is 18°C with light rain...",
  "error": null,
  "metadata": {
    "location": "Paris, FR",
    "units": "metric",
    "data_source": "open-meteo",
    "timestamp": "2024-01-01T12:00:00Z"
  }
}
```

#### Response Format (ERROR)

```json
{
  "success": false,
  "response": null,
  "error": "Unable to determine location from request",
  "metadata": {}
}
```

---

## Status Protocol (CRITICAL)

The agent MUST emit status messages to **stdout** exactly in the following format:

```
STATUS:starting:Initializing Weather Agent
STATUS:running:Server started on port {port}
STATUS:executing:Processing request
STATUS:waiting:Idle, waiting for requests
STATUS:failed:{error_message}
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

- Status lines MUST be flushed immediately.
- `STATUS:executing` MUST be emitted at the start of request processing.
- `STATUS:waiting` MUST be emitted after request completion (success or failure).

---

## Weather Data Requirements

### Data Source

- The agent MUST use a **real-time public weather API**, such as:
  - Open-Meteo (preferred, no API key)
  - OpenWeatherMap (allowed, API key via environment variable)

### Required Data Points

For **current conditions**:
- Temperature
- Feels-like temperature (if available)
- Weather description (clear, rain, snow, etc.)
- Wind speed
- Precipitation (if present)

For **forecast**:
- Daily high and low temperatures
- General conditions per day
- Precipitation chance (if available)

---

## Core Behavior Specification

### 1. Location Resolution

The agent MUST:
- Extract location from natural language (e.g., “London”, “Tokyo”, “90210”)
- Use `context.location` if provided
- If no location can be determined:
  - Return `success: false`
  - Error message:  
    **"Unable to determine location from request"**

---

### 2. Units Handling

- Default units: **metric (°C, km/h)**
- If request contains:
  - “Fahrenheit” → use imperial
  - “Celsius” → use metric
- Units used MUST be reflected in the response text and metadata.

---

### 3. Response Style (MANDATORY)

All responses MUST:
- Be written in **plain, friendly English**
- Avoid meteorological jargon
- Use short paragraphs or bullet points
- Include units explicitly (°C, °F, km/h, mph)

✅ Correct:
> “It’s currently 22°C and sunny in Madrid. Winds are light at about 10 km/h.”

❌ Incorrect:
> “Surface temperatures indicate anticyclonic dominance.”

---

## EXACT Response Behavior by Request Type

### A. Current Weather Request

**Example Input:**
```json
{
  "request": "What's the weather in Berlin right now?"
}
```

**Response Text MUST:**
1. State location
2. State current temperature
3. Describe conditions
4. Mention wind and precipitation if relevant

**Example Response:**
```
Right now in Berlin, it’s 16°C with partly cloudy skies.
Winds are light at around 8 km/h, and there’s no rain at the moment.
```

---

### B. Forecast Request

**Example Input:**
```json
{
  "request": "Will it rain in Seattle this week?"
}
```

**Default Behavior:**
- Provide a **3-day forecast**
- Mention rain chances explicitly

**Example Response:**
```
Here’s a quick look at the next few days in Seattle:

• Today: Cloudy with occasional rain, high of 14°C  
• Tomorrow: Rain likely throughout the day, high of 13°C  
• Day after tomorrow: Mostly cloudy with a chance of showers, high of 15°C
```

---

### C. Weather Explanation Request

**Example Input:**
```json
{
  "request": "Why is it so windy today in Chicago?"
}
```

**Response MUST:**
- Provide a simple explanation
- Avoid technical pressure-system terminology unless simplified

**Example Response:**
```
It’s windy in Chicago today because a weather system is moving through the area.
As it passes, it creates pressure differences in the atmosphere, which causes the air to move faster — that’s what we feel as strong wind.
```

---

### D. Ambiguous or Unsupported Requests

**Example Input:**
```json
{
  "request": "Tell me about the weather"
}
```

**Response MUST:**
```
I can help with that — just let me know which city or location you’re interested in.
```

---

## README.md Requirements

The `README.md` MUST include:
- Agent purpose
- Supported request examples
- How to run the agent
- Required environment variables (if any)
- Example curl request

---

## Summary of Key Constraints

✅ MUST use HTTP A2A protocol  
✅ MUST implement all required endpoints  
✅ MUST follow directory structure exactly  
✅ MUST emit STATUS lines  
✅ MUST provide clear, friendly weather explanations  
✅ MUST return structured JSON responses  

---

**End of Specification — weather_agent**
```