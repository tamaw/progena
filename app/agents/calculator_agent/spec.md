# Build Spec: calculator_agent

> Task: Build the calculator app again. no need for human-like responses it's a calculator. simple calculator. We're really just testing the agent building capabilities more than anything

**Build Description:** An A2A agent that accepts simple addition expressions (e.g., '5 + 5') and returns the computed sum without conversational or human-like responses.

**Project Name:** calculator_agent
> Type: agent
> Generated at: 2026-02-06T00:13:30.208530+00:00

---

```markdown
# Agent Structure Specification — calculator_agent

## Project Overview

**Project Name:** calculator_agent  
**Agent Name:** calculator_agent  
**Agent Class:** CalculatorAgent  

**Build Description:**  
An A2A-compliant HTTP agent that accepts **simple addition expressions** (e.g., `"5 + 5"`) and returns the **computed numeric sum**.  
This agent is **non-conversational** and **non-human-like**. It behaves as a deterministic calculator only.

The primary goal is to validate agent construction, routing, and protocol compliance—not advanced reasoning.

---

## Directory Structure (CRITICAL — MUST FOLLOW EXACTLY)

**DO NOT create files directly in `app/agents/`. ALL agent files MUST go in a subdirectory.**

Create the agent in the subdirectory:

```
app/agents/calculator_agent/
```

With these **EXACT paths**:

```
app/agents/calculator_agent/__init__.py
app/agents/calculator_agent/agent.py
app/agents/calculator_agent/README.md
app/agents/calculator_agent/tests/test_calculator_agent.py
```

❌ WRONG  
`app/agents/calculator_agent.py`  

✅ CORRECT  
`app/agents/calculator_agent/agent.py`

---

## Agent Type

✅ **HTTP Agent (A2A protocol)**  
❌ STDIO agent is NOT used for this build

---

## CalculatorAgent — Class Requirements

The agent MUST define the following **class-level attributes**:

```python
AGENT_ID = "calculator_agent"
AGENT_NAME = "Calculator Agent"
DESCRIPTION = "Evaluates simple addition expressions and returns the numeric sum."
CAPABILITIES = ["addition", "arithmetic", "calculator"]
```

---

## Supported Functionality

### ✅ Supported Input

The agent supports **ONLY simple addition** of two numeric operands.

Valid examples:
- `"5 + 5"`
- `"10+20"`
- `"3.5 + 2.5"`
- `"-4 + 10"`

Operands:
- Integers or floats
- Optional whitespace around numbers and `+`

Operator:
- **Only `+` is supported**

---

### ❌ Unsupported Input

The agent MUST NOT support:
- Subtraction (`-`)
- Multiplication (`*`)
- Division (`/`)
- Multiple operators (`1 + 2 + 3`)
- Natural language (`"what is 5 plus 5?"`)
- Variables or symbols

---

## Response Behavior (STRICT)

### ✅ Success Response Format

For valid addition expressions, the agent MUST return:

```json
{
  "success": true,
  "response": "<computed_sum>",
  "error": null,
  "metadata": {}
}
```

#### Response Value Rules

- `response` MUST be:
  - A **string**
  - Containing **only the numeric result**
  - No extra text, punctuation, or explanation

Examples:

| Input Request | Response |
|---------------|----------|
| `"5 + 5"` | `"10"` |
| `"2.5 + 2.5"` | `"5.0"` |
| `"-1 + 4"` | `"3"` |

❌ INVALID RESPONSES:
- `"The answer is 10"`
- `"10 ✅"`
- `"5 + 5 = 10"`

---

### ❌ Error Handling

If the input does NOT match a valid addition expression, return:

```json
{
  "success": false,
  "response": null,
  "error": "Invalid addition expression",
  "metadata": {}
}
```

This applies to:
- Missing operands
- Non-numeric values
- Unsupported operators
- Empty or null request strings

---

## HTTP Endpoint Requirements (MANDATORY)

### 1. GET `/.well-known/agent.json`

**Purpose:** Agent discovery (A2A standard)

Response JSON:

```json
{
  "name": "Calculator Agent",
  "description": "Evaluates simple addition expressions and returns the numeric sum.",
  "version": "0.1.0",
  "protocol": "a2a",
  "capabilities": ["addition", "arithmetic", "calculator"],
  "endpoint": "http://localhost:{port}"
}
```

---

### 2. GET `/health`

**Purpose:** Health monitoring

Response JSON:

```json
{
  "status": "healthy",
  "name": "Calculator Agent",
  "version": "0.1.0",
  "uptime_seconds": <float>,
  "timestamp": "<ISO-8601 UTC timestamp>"
}
```

---

### 3. POST `/` — Main Invocation Endpoint

#### Request Format

```json
{
  "request": "5 + 5",
  "context": {},
  "id": "optional-id"
}
```

- Only the `request` field is used
- `context` is ignored
- `id` is ignored

---

## Calculator Logic Specification

Inside `CalculatorAgent.process(request: str, context: dict)`:

1. Trim leading/trailing whitespace from `request`
2. Match against a regex equivalent to:
   ```
   ^\s*(-?\d+(\.\d+)?)\s*\+\s*(-?\d+(\.\d+)?)\s*$
   ```
3. Parse both operands as `float`
4. Compute the sum
5. Convert result to string:
   - Remove trailing `.0` ONLY if result is mathematically an integer
6. Return the result string

No logging, no explanation, no formatting beyond the numeric string.

---

## Status Protocol (STDOUT — REQUIRED)

The agent MUST emit the following status lines:

```
STATUS:starting:Initializing Calculator Agent
STATUS:running:Server started on port {port}
STATUS:executing:Processing request
STATUS:waiting:Idle, waiting for requests
STATUS:failed:{error_message}
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

- `STATUS:executing` MUST be emitted for every POST `/` request
- After completion, return to `STATUS:waiting`

---

## README.md Requirements

`app/agents/calculator_agent/README.md` MUST include:

- Agent purpose
- Supported input examples
- Explicit statement:
  > “This agent supports addition only. All other operations are rejected.”
- Example curl request and response

---

## Testing Requirements

### Test File Location

```
app/agents/calculator_agent/tests/test_calculator_agent.py
```

---

### Required Tests

#### 1. Health Endpoint Test
- Confirms `/health` returns HTTP 200
- Confirms `"status": "healthy"`

#### 2. Agent Card Test
- Confirms agent card contains:
  - `name`
  - `capabilities`
  - `endpoint`

#### 3. Valid Addition Test

Input:
```json
{ "request": "5 + 5", "context": {} }
```

Assertions:
- `success == true`
- `response == "10"`

---

#### 4. Invalid Expression Test

Input:
```json
{ "request": "5 * 5", "context": {} }
```

Assertions:
- `success == false`
- `error == "Invalid addition expression"`

---

## Non-Goals (Explicit)

The agent MUST NOT:
- Explain calculations
- Ask clarifying questions
- Handle multiple operations
- Maintain conversation state
- Use FastMCP tools
- Use natural language responses

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

## Summary

This specification defines a **minimal, deterministic, A2A-compliant calculator agent** whose sole responsibility is:

> **Input:** `"a + b"`  
> **Output:** `"a+b"`

Nothing more.
```