# Build Spec: schedule_task

> Task: Build me a hello world agent that responds with 'Hello World!' to any message
> Generated at: 2026-02-01T11:08:47.967612+00:00

---

```markdown
# Agent Structure Specification — schedule_task

This document defines the complete build specification for the **schedule_task** agent.  
The agent is a minimal “Hello World” HTTP agent that responds with the exact string **`Hello World!`** to **any** valid request, regardless of input content.

---

## 1. Directory Structure (CRITICAL — MUST FOLLOW EXACTLY)

**DO NOT create files directly in `app/agents/`. ALL agent files MUST go in a subdirectory.**

Create the agent in the subdirectory:

```
app/agents/schedule_task/
```

With these EXACT paths:

- `app/agents/schedule_task/__init__.py`  
  - Must export the main agent class `ScheduleTask`

- `app/agents/schedule_task/agent.py`  
  - Main agent implementation (**REQUIRED**)

- `app/agents/schedule_task/README.md`  
  - Agent documentation

❌ WRONG:
- `app/agents/schedule_task.py`
- `app/agents/schedule_task_agent.py`

✅ CORRECT:
- `app/agents/schedule_task/agent.py`

---

## 2. Agent Identity and Metadata

The agent MUST define the following class-level attributes:

| Attribute | Value |
|---------|------|
| `AGENT_ID` | `"schedule_task"` |
| `AGENT_NAME` | `"Schedule Task"` |
| `DESCRIPTION` | `"A simple hello world agent that responds with 'Hello World!' to any request."` |
| `CAPABILITIES` | `["hello_world"]` |

---

## 3. Transport Type

✅ **HTTP Agent (A2A Protocol)**  
The agent MUST implement an HTTP server using `aiohttp` and fully comply with the A2A-style endpoints defined below.

No base classes may be inherited.  
The agent must be fully self-contained.

---

## 4. Required HTTP Endpoints (CRITICAL)

### 4.1 GET `/.well-known/agent.json` — Agent Card

**Purpose:**  
Expose agent metadata for discovery.

**Response JSON (EXACT FIELDS REQUIRED):**
```json
{
  "name": "Schedule Task",
  "description": "A simple hello world agent that responds with 'Hello World!' to any request.",
  "version": "0.1.0",
  "protocol": "a2a",
  "capabilities": ["hello_world"],
  "endpoint": "http://localhost:{port}"
}
```

**Behavior Notes:**
- `{port}` MUST be replaced with the runtime port passed via CLI.
- The response must always be HTTP 200.

---

### 4.2 GET `/health` — Health Check

**Purpose:**  
Allow the agent manager to verify liveness.

**Response JSON:**
```json
{
  "status": "healthy",
  "name": "Schedule Task",
  "version": "0.1.0",
  "uptime_seconds": <float>,
  "timestamp": "<ISO-8601 UTC timestamp>"
}
```

**Behavior Notes:**
- `uptime_seconds` is calculated from agent startup time.
- `timestamp` MUST be generated at request time.
- Response must always be HTTP 200 while the agent is running.

---

### 4.3 POST `/` — Main Request Handler

**Purpose:**  
Handle all incoming agent requests.

#### Request Format
```json
{
  "request": "any value",
  "context": {},
  "id": "optional-request-id"
}
```

#### Response Format (SUCCESS)
```json
{
  "success": true,
  "response": "Hello World!",
  "error": null,
  "metadata": {}
}
```

#### Response Format (FAILURE — UNEXPECTED ONLY)
```json
{
  "success": false,
  "response": null,
  "error": "<error message>",
  "metadata": {}
}
```

---

## 5. Core Agent Behavior (CRITICAL)

### 5.1 Primary Logic

The agent MUST behave as follows:

- **For ANY valid POST request to `/`:**
  - Ignore:
    - `request` content
    - `context`
    - `id`
  - ALWAYS return:
    ```
    Hello World!
    ```

✅ The response MUST:
- Match the string **exactly**
- Be case-sensitive
- Include the exclamation mark
- Contain no extra whitespace
- Contain no formatting, markdown, or prefixes

✅ Example inputs and outputs:

| Input `request` value | Output `response` |
|----------------------|------------------|
| `"hi"` | `"Hello World!"` |
| `""` | `"Hello World!"` |
| `"schedule a task"` | `"Hello World!"` |
| `123` | `"Hello World!"` |
| `null` | `"Hello World!"` |

---

### 5.2 Error Handling Rules

- If the request body is **valid JSON**, the agent MUST return success.
- The agent MUST NOT fail based on request content.
- Errors are only permitted for:
  - Invalid JSON
  - Internal server errors

---

## 6. Internal Processing Method

The agent MUST implement a method with the following semantics:

```python
async def process(self, request: str, context: dict) -> str:
```

### Process Method Rules

- Inputs:
  - `request`: ignored
  - `context`: ignored
- Output:
  - Must ALWAYS return the string:
    ```
    Hello World!
    ```

No conditional logic is allowed in this method.

---

## 7. Status Protocol (STDOUT — CRITICAL)

The agent MUST emit status lines to **stdout** in the following format:

```
STATUS:<state>:<message>
```

### Required Status Emissions

| Lifecycle Event | Required Output |
|----------------|----------------|
| Startup begins | `STATUS:starting:Initializing Schedule Task` |
| Server ready | `STATUS:running:Server started on port {port}` |
| Request received | `STATUS:executing:Processing request` |
| After response | `STATUS:waiting:Idle` |
| Fatal error | `STATUS:failed:<error message>` |
| Shutdown initiated | `STATUS:stopping:Shutting down` |
| Shutdown complete | `STATUS:stopped:Agent terminated` |

---

## 8. Runtime Requirements

- The agent MUST:
  - Accept `--port` as a required CLI argument
  - Bind to `localhost`
  - Run indefinitely until externally terminated
- Python version: **3.10+**
- Required library:
  - `aiohttp`

---

## 9. `__init__.py` Requirements

`app/agents/schedule_task/__init__.py` MUST:

- Import and export the `ScheduleTask` class
- Contain no runtime logic

✅ Example intent (not code requirement):
```python
from .agent import ScheduleTask

__all__ = ["ScheduleTask"]
```

---

## 10. README.md Requirements

`README.md` MUST include:

- Agent name
- Description
- How to run the agent
- Example curl request
- Example response

The README MUST clearly state:

> “This agent always responds with `Hello World!` regardless of input.”

---

## 11. Summary of Guaranteed Behavior

✅ Always returns: **`Hello World!`**  
✅ Ignores all inputs  
✅ HTTP + A2A compliant  
✅ Deterministic and stateless  
✅ No side effects  

---

**END OF SPECIFICATION**
```