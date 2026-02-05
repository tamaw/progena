# Build Spec: btc_price_agent

> Task: alright build for me an agent which returns the current price of BTC in USD.

**Build Description:** An A2A agent that retrieves and returns the current Bitcoin (BTC) price in USD from a reliable market data source when invoked.

**Project Name:** btc_price_agent
> Generated at: 2026-02-05T01:16:53.585779+00:00

---

```markdown
# Agent Structure Specification — btc_price_agent

## Project Overview

**Project Name:** btc_price_agent  
**Agent Name:** btc_price_agent  
**Agent Class:** BtcPriceAgent  

**Purpose:**  
An A2A-compliant HTTP agent that retrieves the **current Bitcoin (BTC) price in USD** from a reliable public market data API and returns it in a clear, deterministic JSON response when invoked.

The agent is designed to be:
- Stateless per request
- Deterministic in output format
- Safe for automation and chaining by other agents

---

## Directory Structure (CRITICAL - MUST FOLLOW EXACTLY)

**ALL files must be placed in a subdirectory under `app/agents/`.**

✅ **Correct Structure**
```
app/
└── agents/
    └── btc_price_agent/
        ├── __init__.py
        ├── agent.py
        └── README.md
```

❌ **Incorrect**
- `app/agents/btc_price_agent.py`
- `app/agents/btc_price_agent_agent.py`

---

## Agent Type

✅ **HTTP Agent (A2A Protocol)**  
The agent exposes HTTP endpoints and runs an aiohttp web server.

---

## External Dependency Requirements

The agent **MUST** retrieve live BTC pricing data from a reliable public API.

### Approved Market Data Sources (choose ONE)

1. **CoinGecko (Preferred)**
   - Endpoint:  
     `https://api.coingecko.com/api/v3/simple/price`
   - Query params:
     ```
     ids=bitcoin
     vs_currencies=usd
     ```
   - Expected response:
     ```json
     {
       "bitcoin": {
         "usd": 43521.42
       }
     }
     ```

2. **Coinbase (Alternative)**
   - Endpoint:  
     `https://api.coinbase.com/v2/prices/BTC-USD/spot`

⚠️ The agent MUST:
- Use HTTPS
- Timeout outbound HTTP requests after **≤ 5 seconds**
- Gracefully handle API failures

---

## Agent Class Requirements

The main class **MUST** be named:

```python
class BtcPriceAgent:
```

### Required Class Attributes

```python
AGENT_ID = "btc_price_agent"
AGENT_NAME = "BTC Price Agent"
DESCRIPTION = "Returns the current Bitcoin (BTC) price in USD using live market data."
CAPABILITIES = [
    "crypto_price_lookup",
    "btc_usd_price",
    "market_data"
]
```

---

## Required HTTP Endpoints

### 1. GET `/.well-known/agent.json` — Agent Card

**Purpose:** A2A discovery

**Response JSON (EXACT SHAPE):**
```json
{
  "name": "BTC Price Agent",
  "description": "Returns the current Bitcoin (BTC) price in USD using live market data.",
  "version": "0.1.0",
  "protocol": "a2a",
  "capabilities": [
    "crypto_price_lookup",
    "btc_usd_price",
    "market_data"
  ],
  "endpoint": "http://localhost:{port}"
}
```

---

### 2. GET `/health` — Health Check

**Purpose:** Monitoring & orchestration

**Response JSON:**
```json
{
  "status": "healthy",
  "name": "BTC Price Agent",
  "version": "0.1.0",
  "uptime_seconds": 123.45,
  "timestamp": "2024-01-01T00:00:00Z"
}
```

---

### 3. POST `/` — Main Request Handler

#### Request Format

```json
{
  "request": "string",
  "context": {},
  "id": "optional-request-id"
}
```

---

## Core Agent Behavior

### Accepted Inputs

The agent **ignores natural language variance** and always returns the BTC price.

Valid `request` examples:
- `"btc price"`
- `"get btc price"`
- `"what is bitcoin worth"`
- `""` (empty string)
- `null`

➡️ **All inputs are treated identically.**

---

### Processing Logic (REQUIRED)

1. Emit:
   ```
   STATUS:executing:Processing request
   ```
2. Fetch live BTC price from the configured API
3. Normalize the price to:
   - Float
   - USD
   - Rounded to **2 decimal places**
4. Return structured JSON response
5. Emit:
   ```
   STATUS:waiting:Idle
   ```

---

## Response Format (SUCCESS)

### HTTP 200 Response

```json
{
  "success": true,
  "response": {
    "asset": "BTC",
    "currency": "USD",
    "price": 43521.42,
    "source": "coingecko",
    "retrieved_at": "2024-01-01T00:00:00Z"
  },
  "error": null,
  "metadata": {
    "request_id": "optional-request-id"
  }
}
```

### Field Definitions

| Field | Type | Description |
|-----|-----|------------|
| `asset` | string | Always `"BTC"` |
| `currency` | string | Always `"USD"` |
| `price` | number | Current BTC price (2 decimals) |
| `source` | string | Data provider identifier |
| `retrieved_at` | ISO-8601 string | UTC timestamp |

---

## Error Handling

### Market Data API Failure

If the external API:
- Times out
- Returns invalid JSON
- Returns non-200 status

✅ **Response: HTTP 500**

```json
{
  "success": false,
  "response": null,
  "error": "Failed to retrieve BTC price from market data provider.",
  "metadata": {}
}
```

✅ Emit status:
```
STATUS:failed:Failed to retrieve BTC price
STATUS:waiting:Idle
```

---

## Status Protocol (MANDATORY)

The agent MUST emit the following lifecycle messages to stdout:

```
STATUS:starting:Initializing BTC Price Agent
STATUS:running:Server started on port {port}
STATUS:executing:Processing request
STATUS:waiting:Idle
STATUS:failed:{error_message}
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

---

## `invoke()` / Processing Semantics

Although implemented via HTTP, the internal processing function MUST behave as follows:

### Input
```python
request: str
context: dict
```

### Output (Success)
```python
{
  "success": True,
  "response": {
    "asset": "BTC",
    "currency": "USD",
    "price": float,
    "source": str,
    "retrieved_at": str
  },
  "error": None,
  "metadata": dict
}
```

### Output (Failure)
```python
{
  "success": False,
  "response": None,
  "error": str,
  "metadata": {}
}
```

---

## README.md Requirements

The `README.md` MUST include:

1. Agent purpose
2. How to run:
   ```bash
   python agent.py --port 8080
   ```
3. Example `curl` request
4. Example JSON response
5. Data source attribution (CoinGecko or Coinbase)

---

## Non-Goals (Explicitly Out of Scope)

- No historical prices
- No multi-asset support
- No caching
- No authentication
- No WebSocket or streaming

---

## Summary

The **btc_price_agent** is a minimal, deterministic A2A HTTP agent whose **sole responsibility** is to return the **current BTC/USD price** in a machine-readable format suitable for automation, orchestration, and chaining by other agents.
```