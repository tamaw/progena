# Build Spec: weather

> Task: build me a weather agent 
```specs
## 🌦 Weather Agent — Specification

### Purpose
The Weather Agent answers weather questions in a human, helpful, calm way, not just with raw data.  
It focuses on practical advice and reassurance, not numbers alone.

---

### Inputs
The agent accepts:
- Location
  - City name (e.g., “Paris”)
  - Or coordinates (lat, lon)
- Optional context
  - Timeframe: now / today / tomorrow / next 7 days
  - User intent: travel, clothing, safety, outdoor plans

Example inputs:
- “Do I need a jacket in Berlin?”
- “Is it safe to go outside tonight in Miami?”
- “What’s the weather like tomorrow morning?”

---

### Data Source
- Weather API (e.g., OpenWeather)
- Uses:
  - Current weather
  - Hourly forecast
  - Daily forecast
  - Weather alerts (if available)

---

### Core Responsibilities
1. Fetch accurate weather data
2. Interpret it
   - Temperature
   - Feels-like
   - Wind
   - Precipitation
   - Severe conditions
3. Translate it into human advice
   - Clothing suggestions
   - Travel/outdoor safety
   - Tone adjusted to conditions

---

### Behavior & Tone
- Calm and conversational
- Reassuring during storms or extreme weather
- Friendly during mild conditions
- Never alarmist
- Never overly technical unless asked

Example:
> “It’s 12°C, but the wind makes it feel cooler. A light jacket will make the walk more comfortable.”

---

### Output Format
- Short paragraph response
- Optional bullet points for clarity
- Avoids raw JSON unless explicitly requested

---
> Generated at: 2026-02-03T00:49:28.664336+00:00

---

```markdown
# 🌦 Weather Agent — Build Specification

## Agent Name
**weather**

## Agent Class
**Weather**

---

## Overview

The **Weather Agent** is an HTTP-based AI agent that provides human-friendly weather explanations and practical advice.  
It prioritizes **clarity, reassurance, and usefulness**, translating raw weather data into actionable guidance for everyday decisions such as clothing, travel, and safety.

The agent complies fully with the A2A HTTP Agent protocol and operates as a **self-contained service**.

---

## Directory Structure (MANDATORY)

All files MUST exist exactly as shown below:

```
app/agents/weather/
├── __init__.py        # Exports the Weather agent class
├── agent.py           # Main HTTP agent implementation
└── README.md          # Agent documentation
```

❌ Do NOT place files directly in `app/agents/`  
✅ Use the `weather/` subdirectory exactly

---

## Agent Metadata

### Required Class Attributes

```python
AGENT_ID = "weather"
AGENT_NAME = "Weather Agent"
DESCRIPTION = "Provides calm, human-friendly weather explanations and practical advice based on real-time forecasts."
CAPABILITIES = [
    "current_weather",
    "forecast_weather",
    "weather_advice",
    "weather_safety_guidance"
]
```

---

## Protocol & Transport

- **Protocol:** A2A
- **Transport:** HTTP (aiohttp)
- **Port:** Provided at runtime via `--port`

---

## Required HTTP Endpoints

### 1. `GET /.well-known/agent.json`

Returns the Agent Card.

**Response (example):**
```json
{
  "name": "Weather Agent",
  "description": "Provides calm, human-friendly weather explanations and practical advice based on real-time forecasts.",
  "version": "0.1.0",
  "protocol": "a2a",
  "capabilities": [
    "current_weather",
    "forecast_weather",
    "weather_advice",
    "weather_safety_guidance"
  ],
  "endpoint": "http://localhost:8000"
}
```

---

### 2. `GET /health`

Health and uptime check.

**Response:**
```json
{
  "status": "healthy",
  "name": "Weather Agent",
  "version": "0.1.0",
  "uptime_seconds": 452.31,
  "timestamp": "2026-02-03T12:00:00Z"
}
```

---

### 3. `POST /` — Main Request Handler

#### Request Format
```json
{
  "request": "Do I need a jacket in Berlin?",
  "context": {
    "timeframe": "today",
    "intent": "clothing"
  },
  "id": "optional-request-id"
}
```

- `request` (string, REQUIRED): Natural language user question
- `context` (object, OPTIONAL):
  - `location`: city name or `{ "lat": ..., "lon": ... }`
  - `timeframe`: now | today | tomorrow | next_7_days
  - `intent`: travel | clothing | safety | outdoor_plans
- `id` (string, OPTIONAL)

#### Response Format
```json
{
  "success": true,
  "response": "Human-readable weather advice",
  "error": null,
  "metadata": {
    "location": "Berlin",
    "timeframe": "today",
    "source": "OpenWeather"
  }
}
```

---

## Status Output Requirements

The agent MUST emit the following status messages to stdout:

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

## Weather Data Integration

### Data Source
- OpenWeather API (or equivalent)

### Required Data Usage
- Current conditions
- Hourly forecast (next 24 hours)
- Daily forecast (up to 7 days)
- Weather alerts (if available)

### Minimum Fields to Extract
- Temperature (°C)
- Feels-like temperature
- Wind speed
- Precipitation (rain/snow probability)
- Severe conditions (storms, heat, cold, alerts)

---

## Core Processing Logic

### Step 1: Parse User Intent
The agent MUST infer:
- Location (explicit or inferred)
- Timeframe (default: **now**)
- Intent (default: **general advice**)

If location is missing:
- Respond with a polite clarification request:
  > “I can help — which city are you asking about?”

---

### Step 2: Fetch Weather Data
- Query the weather API using resolved location
- Select appropriate forecast range based on timeframe

---

### Step 3: Interpret Conditions

The agent MUST convert raw data into **qualitative judgments**, such as:
- “Cool and breezy”
- “Mild with occasional showers”
- “Hot and humid”
- “Stormy later tonight”

---

### Step 4: Generate Human Advice

#### Tone Rules
- ✅ Calm and conversational
- ✅ Reassuring during storms or extremes
- ❌ No panic language
- ❌ No raw meteorological jargon unless asked

---

## EXACT RESPONSE BEHAVIOR

### General Rules
- Output MUST be plain text (not JSON)
- Length: 1 short paragraph (2–4 sentences)
- Optional bullet points allowed for clarity
- Avoid exact numbers unless useful
- Use °C by default

---

### Example Responses (MANDATORY STYLE)

#### Clothing Question
**Input:**  
“Do I need a jacket in Berlin?”

**Response:**
> “It’s on the cool side today in Berlin, and the breeze makes it feel a bit colder than it looks. A light jacket will keep you comfortable, especially if you’ll be outside for a while.”

---

#### Safety Question (Storm)
**Input:**  
“Is it safe to go outside tonight in Miami?”

**Response:**
> “There are storms expected in Miami tonight, with heavy rain and gusty winds at times. It’s best to keep outdoor plans short and stay indoors during the strongest storms. If you do head out, keep an eye on local alerts.”

---

#### Forecast Question
**Input:**  
“What’s the weather like tomorrow morning?”

**Response:**
> “Tomorrow morning looks mild and mostly dry, with comfortable temperatures for getting around. It should be a good window for errands or a walk before conditions change later in the day.”

---

#### Missing Location
**Input:**  
“What’s the weather like?”

**Response:**
> “I can help with that — which city or location are you asking about?”

---

## Error Handling

- Weather API failure:
  - Respond calmly:
    > “I’m having trouble reaching the weather service right now. Please try again in a moment.”
- Invalid location:
  - Ask for clarification, never blame the user

---

## README.md Requirements

The README MUST include:
- Agent purpose
- Example requests and responses
- Supported timeframes and intents
- Setup instructions (API key via environment variable)
- Port usage instructions

---

## Non-Goals

The Weather Agent MUST NOT:
- Output raw weather JSON
- Use alarmist or dramatic language
- Give medical or emergency instructions
- Act as a general chatbot outside weather topics

---

## Success Criteria

The Weather Agent is considered complete when:
- All required endpoints function correctly
- Responses match the tone and examples exactly
- Advice is practical, calm, and human-centered
- Agent passes health checks and emits proper status messages

---

✅ **End of Weather Agent Build Specification**
```

## Iteration 2 Updates

### Gaps to Address
- Criterion 'Task completed successfully' not met (score: 0.20)