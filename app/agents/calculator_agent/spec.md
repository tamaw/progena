# Build Spec: calculator_agent

> Task: lets build the calculator agent again

**Build Description:** An A2A calculator agent that parses simple addition expressions (e.g., '5 + 5') and returns the computed numeric result without conversational text.

**Project Name:** calculator_agent
> Type: agent
> Generated at: 2026-02-06T11:09:20.239053+00:00

---

```markdown
# Agent Structure Specification — calculator_agent

## Project Overview

**Project Name:** calculator_agent  
**Agent Name:** calculator_agent  
**Agent Class:** CalculatorAgent  

**Build Description:**  
An A2A-compatible calculator agent that parses and evaluates **simple addition expressions only** (e.g., `"5 + 5"`) and returns the **computed numeric result**.  
The agent MUST return **only the numeric result as a string**, with **no conversational text, no explanations, and no formatting beyond the number itself**.

---

## Directory Structure (CRITICAL - MUST FOLLOW EXACTLY)

**DO NOT create files directly in `app/agents/`. ALL agent files MUST go in a subdirectory.**

Create the agent in the subdirectory:

```
app/agents/calculator_agent/
```

With these EXACT paths:

- `app/agents/calculator_agent/__init__.py`  
  - Exports the `CalculatorAgent` class

- `app/agents/calculator_agent/agent.py`  
  - Main agent implementation (REQUIRED)

- `app/agents/calculator_agent/README.md`  
  - Agent documentation

- `app/agents/calculator_agent/tests/`
  - `test_calculator_agent.py`

❌ WRONG:
```
app/agents/calculator_agent.py
app/agents/calculator_agent_agent.py
```

✅ CORRECT:
```
app/agents/calculator_agent/agent.py
```

---

## Agent Type

**HTTP Agent (A2A Protocol)**  
The agent MUST implement all required HTTP endpoints and status output behavior exactly as specified.

---

## Agent Identity & Metadata

### Required Class Attributes (CalculatorAgent)

```python
AGENT_ID = "calculator_agent"
AGENT_NAME = "Calculator Agent"
DESCRIPTION = "Parses and evaluates simple addition expressions and returns the numeric result."
CAPABILITIES = ["addition", "arithmetic", "calculator"]
```

---

## HTTP Endpoints (CRITICAL)

### 1. GET `/.well-known/agent.json` — Agent Card

Returns a valid A2A agent card.

**Response JSON (example):**
```json
{
  "name": "Calculator Agent",
  "description": "Parses and evaluates simple addition expressions and returns the numeric result.",
  "version": "0.1.0",
  "protocol": "a2a",
  "capabilities": ["addition", "arithmetic", "calculator"],
  "endpoint": "http://localhost:{port}"
}
```

---

### 2. GET `/health` — Health Check

**Response JSON:**
```json
{
  "status": "healthy",
  "name": "Calculator Agent",
  "version": "0.1.0",
  "uptime_seconds": 123.45,
  "timestamp": "2024-01-01T00:00:00+00:00"
}
```

---

### 3. POST `/` — Main Request Handler

#### Request Format

```json
{
  "request": "5 + 5",
  "context": {},
  "id": "optional-request-id"
}
```

#### Response Format (SUCCESS)

```json
{
  "success": true,
  "response": "10",
  "error": null,
  "metadata": {}
}
```

#### Response Format (FAILURE)

```json
{
  "success": false,
  "response": null,
  "error": "Error message",
  "metadata": {}
}
```

---

## Core Behavior Specification

### Supported Input

The agent MUST support:

- **Simple addition expressions only**
- Format:
  ```
  <number> + <number>
  ```
- Numbers MAY be:
  - Integers: `5`, `10`, `-3`
  - Floats: `2.5`, `-4.75`

Valid examples:
- `"5 + 5"`
- `"10+20"`
- `"3.5 + 4.5"`
- `"-2 + 8"`

---

### Parsing Rules

- Whitespace around numbers and `+` is OPTIONAL
- Only ONE `+` operator is allowed
- Expression MUST contain exactly two operands

---

### Output Rules (STRICT)

- The response MUST be:
  - The computed numeric result
  - Returned as a **string**
  - With **no additional text**
- No units, no explanations, no prefixes, no suffixes

✅ CORRECT:
```json
"response": "10"
```

❌ INCORRECT:
```json
"response": "The result is 10"
"response": "10.0 apples"
"response": "Here you go: 10"
```

---

## Error Handling Rules

If the input does NOT conform to supported addition expressions, the agent MUST:

- Return `success: false`
- Provide a clear error message
- NOT attempt to guess user intent

### Invalid Input Cases

| Input              | Behavior |
|-------------------|----------|
| `"hello"`         | Error: invalid expression |
| `"5 - 3"`         | Error: unsupported operation |
| `"5 * 5"`         | Error: unsupported operation |
| `"5 + 5 + 5"`     | Error: too many operands |
| `"+"`             | Error: invalid expression |
| `""`              | Error: empty request |
| `null`            | Error: missing request |

**Example Error Response:**
```json
{
  "success": false,
  "response": null,
  "error": "Invalid addition expression",
  "metadata": {}
}
```

---

## `process()` Method Specification

### Method Signature

```python
async def process(self, request: str, context: dict) -> str
```

### Behavior

1. Trim whitespace from `request`
2. Validate request is non-empty
3. Parse the expression:
   - Split on `+`
   - Ensure exactly two operands
4. Convert operands to `float`
5. Compute the sum
6. Return:
   - Integer result without decimal if whole number
   - Float result otherwise

### Return Value Rules

| Input        | Return Value |
|-------------|--------------|
| `"5 + 5"`   | `"10"` |
| `"2.5 + 2"` | `"4.5"` |
| `"-1 + 1"`  | `"0"` |

---

## Status Protocol (MANDATORY)

The agent MUST emit the following status lines to stdout:

```
STATUS:starting:Initializing Calculator Agent
STATUS:running:Server started on port {port}
STATUS:executing:Processing request
STATUS:waiting:Idle, waiting for requests
STATUS:failed:{error message}
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

---

## Testing Requirements

### Test Directory

```
app/agents/calculator_agent/tests/
```

### Test File

```
test_calculator_agent.py
```

### Required Tests

1. Health endpoint returns `status: healthy`
2. Agent card contains:
   - `name`
   - `capabilities`
3. Valid addition request:
   - `"5 + 5"` → `"10"`
4. Invalid request:
   - `"5 - 3"` → `success: false`
5. Float addition:
   - `"2.5 + 2.5"` → `"5"`

---

## README.md Requirements

The README MUST include:

- Agent purpose
- Supported input format
- Example requests and responses
- Error behavior
- How to run the agent

---

## Registration

After implementation:

1. Start the agent
2. Run the system tool:
   ```
   rediscover_agents
   ```
3. Verify `calculator_agent` appears in `registry.json`

---

## Summary (Non-Negotiable Constraints)

- ✅ Addition ONLY
- ✅ No conversational output
- ✅ Numeric result only
- ✅ Strict A2A compliance
- ✅ Exact directory structure
- ✅ Explicit error handling

This specification MUST be followed exactly.
```