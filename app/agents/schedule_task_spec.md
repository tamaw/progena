# Build Spec: schedule_task

> Task: Build me a hello world agent that responds with 'Hello World!' to any message
> Generated at: 2026-02-01T11:48:58.044883+00:00

---

```markdown
# Agent Structure Specification — schedule_task

This document defines the **complete build specification** for the `schedule_task` agent.  
It is a **Hello World HTTP agent** that responds with the **exact text** `Hello World!` to **any incoming request**, regardless of input content.

---

## Directory Structure (CRITICAL - MUST FOLLOW EXACTLY)

**DO NOT create files directly in `app/agents/`. ALL agent files MUST go in a subdirectory.**

Create the agent in the subdirectory:

```
app/agents/schedule_task/
```

With these **EXACT paths**:

- `app/agents/schedule_task/__init__.py`  
  - Must export the main agent class `ScheduleTask`

- `app/agents/schedule_task/agent.py`  
  - Main agent implementation (REQUIRED)

- `app/agents/schedule_task/README.md`  
  - Agent documentation

❌ WRONG:
```
app/agents/schedule_task.py
app/agents/schedule_task_agent.py
```

✅ CORRECT:
```
app/agents/schedule_task/agent.py
```

---

## Agent Identity (REQUIRED)

The agent MUST define the following **class-level attributes** exactly:

```python
AGENT_ID = "schedule_task"
AGENT_NAME = "Schedule Task"
DESCRIPTION = "A simple hello world agent that always responds with 'Hello World!'"
CAPABILITIES = ["hello_world", "test_agent"]
```

---

## Transport Type

✅ **HTTP Agent (A2A protocol)**  
This agent MUST be implemented as an HTTP server using `aiohttp`.

No base classes may be used.  
The agent must be fully self-contained.

---

## Required HTTP Endpoints

### 1. GET `/.well-known/agent.json` — Agent Card (A2A Standard)

**Purpose:**  
Allows agent discovery and introspection.

**Response JSON (EXACT SHAPE):**
```json
{
    "name": "Schedule Task",
    "description": "A simple hello world agent that always responds with 'Hello World!'",
    "version": "0.1.0",
    "protocol": "a2a",
    "capabilities": ["hello_world", "test_agent"],
    "endpoint": "http://localhost:{port}"
}
```

**Behavior Rules:**
- `{port}` must be the actual port passed via CLI
- No additional fields are allowed
- Values must match the agent identity exactly

---

### 2. GET `/health` — Health Check

**Purpose:**  
Used by the agent manager to verify liveness.

**Response JSON (EXACT SHAPE):**
```json
{
    "status": "healthy",
    "name": "Schedule Task",
    "version": "0.1.0",
    "uptime_seconds": 12.34,
    "timestamp": "2024-01-01T00:00:00Z"
}
```

**Behavior Rules:**
- `uptime_seconds` = seconds since agent startup (float)
- `timestamp` MUST be UTC ISO-8601
- Always return `"status": "healthy"` while running

---

### 3. POST `/` — Main Request Handler

**Purpose:**  
Handles all incoming requests.

#### Request Format (INPUT)

```json
{
    "request": "any value",
    "context": {},
    "id": "optional-request-id"
}
```

✅ The agent **MUST NOT** validate or reject inputs.  
✅ Missing fields must be handled gracefully.

---

#### Response Format (OUTPUT — EXACT)

```json
{
    "success": true,
    "response": "Hello World!",
    "error": null,
    "metadata": {}
}
```

---

## Core Agent Behavior (CRITICAL)

### Universal Response Rule

✅ **For EVERY possible input**, the agent MUST respond with:

```
Hello World!
```

### Input Handling Rules

The following inputs MUST ALL produce the same output:

| Input Condition | Required Behavior |
|-----------------|------------------|
| Empty request body | Respond `"Hello World!"` |
| Missing `request` field | Respond `"Hello World!"` |
| `request` is a string | Respond `"Hello World!"` |
| `request` is a number | Respond `"Hello World!"` |
| `request` is null | Respond `"Hello World!"` |
| Large payload | Respond `"Hello World!"` |
| Any `context` value | Ignored |
| Any `id` value | Ignored |

⚠️ **No conditional logic is allowed** based on input.

---

## Internal Processing Logic

### `process()` Method Requirements

The agent MUST define an async method:

```python
async def process(self, request: str, context: dict) -> str:
```

**Implementation Rule:**

- The method MUST ignore all arguments
- The method MUST always return:

```python
"Hello World!"
```

No logging, formatting, or modification is allowed.

---

## Status Protocol (MANDATORY)

The agent MUST emit the following status lines to **stdout**:

### On Startup
```
STATUS:starting:Initializing Schedule Task
STATUS:running:Server started on port {port}
```

### On Each Request
```
STATUS:executing:Processing request
STATUS:waiting:Idle
```

### On Failure
```
STATUS:failed:{error_message}
```

### On Shutdown
```
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

✅ Status messages must be flushed immediately  
✅ Status format must be EXACT

---

## CLI Requirements

The agent MUST be runnable via:

```bash
python agent.py --port 8080
```

- `--port` is REQUIRED
- The server must bind to `localhost`

---

## `__init__.py` Requirements

`app/agents/schedule_task/__init__.py` MUST export:

```python
from .agent import ScheduleTask

__all__ = ["ScheduleTask"]
```

---

## README.md Requirements

`README.md` MUST include:

- Agent name
- Description
- Capabilities
- How to run the agent
- Example request
- Example response showing **Hello World!**

---

## Summary of Guaranteed Behavior

✅ Always returns **exactly**:
```
Hello World!
```

✅ Ignores all input  
✅ Stateless  
✅ Deterministic  
✅ A2A compliant  
✅ Health-checkable  
✅ Discoverable  

---

✅ **This specification is complete and sufficient to implement the `schedule_task` Hello World agent without ambiguity.**
```