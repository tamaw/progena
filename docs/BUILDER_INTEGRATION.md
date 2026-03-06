# Progenitor × Builder Integration

This document explains how Progenitor delegates code generation tasks to Builder asynchronously using the A2A protocol.

## Architecture Overview

```
User Request
    ↓
Progenitor (Main Agent Loop)
    ├─ Receives: "Create a README for Builder"
    ├─ Decides: This is a code generation task
    ├─ Invokes: invoke_builder system tool
    │    ↓
    │ invoke_builder Tool
    │    ├─ Prepares A2A message/send request
    │    ├─ Posts to Builder at http://localhost:8108
    │    └─ Returns immediately with task_id
    │
    └─ Returns: task_id + "Task queued for execution"

Builder (Background Task)
    ├─ Receives: A2A message/send POST request
    ├─ Accepts: Returns 200 OK immediately
    ├─ Queues: Creates git worktree, plans work
    ├─ Executes: Runs plan → build → test → lint → pr skills
    └─ Completes: Creates pull request
         ├─ Streams: TaskStatusUpdateEvent updates via event queue
         └─ Ends: Final TaskArtifactUpdateEvent with result
```

## Key Components

### 1. Agent Configuration (`data/config/agents.json`)

Registers all agents including Builder:

```json
{
  "builder": {
    "kind": "a2a_agent",
    "version": "0.1.0",
    "description": "Minimal DeepAgent builder using GitHub Copilot...",
    "capabilities": [
      "build", "plan", "test", "lint", "pr", "async_tasks"
    ],
    "url": "http://localhost:8108",
    "status": "stopped"
  }
}
```

**Capabilities** tell Progenitor's LLM when to invoke this agent.

### 2. System Tool: `invoke_builder` 

Located in `app/progenitor/tools/invoke_builder.py`

**Purpose**: Invoke Builder via A2A protocol

**When used**: 
- User asks to build something: "Create a README for Builder"
- LLM decides this matches "build" capability
- System tool is invoked automatically

**Flow**:
```python
# 1. Tool receives request from LLM
request_text = "Create a comprehensive README for Builder..."

# 2. Prepare A2A request
payload = {
    "jsonrpc": "2.0",
    "id": request_id,
    "method": "message/send",  # A2A method
    "params": {
        "message": {
            "kind": "message",
            "message_id": message_id,
            "role": "user",
            "parts": [{"kind": "text", "text": request_text}],
        },
        "metadata": {}
    }
}

# 3. POST to Builder
POST http://localhost:8108/  # A2A server
    ↓ (blocks until response, but agent processes asynchronously)
    ↓ (expected timeout = OK, work continues in background)

# 4. Return task_id to caller
{
    "task_id": "copilot_a1b2c3d4e5f6g7h8",
    "status": "accepted",
    "message": "Task queued for execution..."
}
```

### 3. A2A Protocol

**Standard**: Agent-to-Agent JSON-RPC 2.0

**Method**: `message/send`

**Request Format**:
```json
{
  "jsonrpc": "2.0",
  "id": "request_uuid",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "message_id": "msg_xyz",
      "role": "user",
      "parts": [
        {
          "kind": "text",
          "text": "Request description..."
        }
      ]
    },
    "metadata": {}
  }
}
```

**Response**: Returns immediately (200 OK) even if task takes hours.

**Async Handling**: 
- Builder spawns background task
- Creates git worktree (isolated)
- Plans work with LLM
- Executes: plan → build → test → lint → pr
- Streams `TaskStatusUpdateEvent` updates to caller
- Returns result via `TaskArtifactUpdateEvent`

## Usage Examples

### Example 1: Direct Invocation via Progenitor

**User**: "Can you create a comprehensive README for Builder?"

**Progenitor's Decision**:
1. Recognizes capability: "build" / "documentation"
2. Selects: invoke_builder tool
3. Delegates: Sends A2A request
4. Returns: task_id to user

**Progenitor's Response**:
```
Task accepted! ID: copilot_a1b2c3d4e5f6g7h8
Builder is processing in the background...
Status updates: Check logs or use list_tasks
```

### Example 2: Direct HTTP Invocation (Script)

```python
import httpx
import json
import uuid

payload = {
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
        "message": {
            "kind": "message",
            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
            "role": "user",
            "parts": [{
                "kind": "text",
                "text": "Create comprehensive README for Builder"
            }]
        },
        "metadata": {}
    }
}

async with httpx.AsyncClient(timeout=30) as client:
    resp = await client.post(
        "http://localhost:8108/",
        json=payload
    )
    result = resp.json()
    task_id = result.get("result", {}).get("task_id", "unknown")
    print(f"Task accepted: {task_id}")
    # Task continues in background...
```

## Configuration & Setup

### 1. Ensure Builder is Registered

File: `data/config/agents.json`

```bash
# This file is auto-loaded by Progenitor on startup
# If missing, run:
$ python -m app.progenitor.service discover_agents
```

### 2. Start Builder  

```bash
# Terminal 1: Start Builder agent
$ python -m uvicorn app.agents.builder.agent:app --port 8108

# Terminal 2: (Optional) Test invocation
$ python scripts/demo_builder.py

# Terminal 3: Start Progenitor (main loop)
$ python -m app.progenitor.main
```

### 3. Verify Integration

**Check agent discovery**:
```bash
$ curl http://localhost:8108/health
{
  "status": "healthy",
  "name": "Builder",
  "version": "0.1.0",
  "uptime_seconds": 42.5
}
```

**Check Progenitor's agent registry**:
```bash
# In Progenitor, ask the LLM:
User: "What agents are available?"
System: Lists all registered agents including Builder
```

## Advanced Topics

### Async Timeout Handling

Builder tasks can take minutes (cloning repo, Copilot generation, testing, PR creation). 

**Default timeout**: 30 seconds (configurable)

**On timeout**: 
- HTTP request times out ✓ (expected, not an error)
- Task continues in background ✓
- Returns `task_id` for monitoring ✓

```python
# In invoke_builder tool:
try:
    resp = await client.post(url, json=payload, timeout=30)
except httpx.TimeoutException:
    # Expected for long-running tasks!
    return ToolResult.ok({
        "task_id": assigned_id,
        "status": "accepted",
        "message": "Timeout expected. Task is running in background."
    })
```

### Status Updates (Event Queue)

Builder streams status updates via A2A TaskStatusUpdateEvent:

```python
# In BuilderExecutor.execute():

# Status: Working
await event_queue.enqueue_event(TaskStatusUpdateEvent(
    task_id=task_id,
    status=TaskStatus(
        state=TaskState.working,  # "working" | "success" | "failed"
        message=_text_message("Starting Builder run..."),
        timestamp=_utc_now_iso(),
    ),
    final=False  # More updates coming
))

# ... do work ...

# Status: Complete with artifact
await event_queue.enqueue_event(TaskArtifactUpdateEvent(
    task_id=task_id,
    artifact=Artifact(
        artifact_id=str(uuid.uuid4()),
        name="pr-link",
        description="Pull request created",
        parts=[Part(root=TextPart(text="https://github.com/.../pull/123"))]
    ),
    final=True
))
```

### Agent Discovery

Auto-discover new agents when they're deployed:

```bash
# In Progenitor (auto on startup, but can re-run):
User: "Rediscover agents"
→ Progenitor calls discover_agents()
→ Scans app/agents/ folder
→ Analyzes each agent with LLM
→ Registers new capabilities
→ Updates data/config/agents.json
```

## Testing

### Quick Integration Test

```bash
# 1. Start Builder
python -m uvicorn app.agents.builder.agent:app --port 8108 &

# 2. Run demo script
python scripts/demo_builder.py

# 3. Monitor logs
tail -f logs/builder/builder.stdout.log
```

### Progenitor Invocation Test

```python
# In Progenitor node test:
from app.progenitor.tools.invoke_builder import InvokeBuilderTool
from app.progenitor.tools.protocol import ToolContext

tool = InvokeBuilderTool()
context = ToolContext(
    user_input="Create README for Builder",
    tool_args=None,
    tool_registry=mock_registry,
    llm_client=mock_llm
)
result = await tool.execute(context)
assert result.success
assert "task_id" in result.output
```

## Troubleshooting

### Builder Not Running

```
Error: Builder not running at http://localhost:8108
```

**Fix**:
```bash
python -m uvicorn app.agents.builder.agent:app --port 8108
```

### Agent Not Registered

```
Error: invoke_builder tool not found
```

**Fix**:
```bash
# Restart Progenitor to auto-discover tools:
python -m app.progenitor.main
```

### Task Timeout (Not Actually an Error)

```
httpx.ReadTimeout: Request timeout after 120s
```

**This is OK!** Builder is still working. Check logs:
```bash
tail -f logs/builder/builder.stdout.log
```

### PR Not Created

Check Builder logs:
```bash
cat logs/builder/builder.stdout.log | grep -A 5 "error\|fail"
```

Common issues:
- GitHub CLI (`gh`) not configured: `gh auth login`
- Copilot CLI not installed: `npm install -g @github/github-cli`
- No internet/GitHub connectivity

## Summary

| Aspect | Details |
|--------|---------|
| **Protocol** | A2A JSON-RPC 2.0 (message/send) |
| **Endpoint** | POST http://localhost:8108/ |
| **Async Model** | Fire-and-forget with task_id tracking |
| **Tool Name** | `invoke_builder` (auto-discovered) |
| **Capabilities** | build, plan, test, lint, pr, async_tasks |
| **Response Time** | Immediate (200 OK) even for long tasks |
| **Status Updates** | TaskStatusUpdateEvent via event queue |
| **Config File** | data/config/agents.json |
| **Demo Script** | scripts/demo_builder.py |

