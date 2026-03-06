# Progenitor ↔ Builder A2A Integration - Implementation Summary

## ✅ What Was Implemented

### 1. **Agent Configuration** 
   - **File**: `data/config/agents.json` (NEW)
   - **Purpose**: Registers Builder and other agents with Progenitor
   - **Key Info**:
     - Builder URL: `http://localhost:8108`
     - Capabilities: build, plan, test, lint, pr, async_tasks
     - Status tracking: Can be "stopped" or assigned a port

### 2. **System Tool: `invoke_builder`**
   - **File**: `app/progenitor/tools/invoke_builder.py` (NEW)
   - **Auto-Discovered**: Yes ✅ (registry auto-discovers in `app.progenitor.tools`)
   - **Purpose**: Invoke Builder asynchronously via A2A protocol
   - **Key Features**:
     - Sends A2A `message/send` JSON-RPC request to Builder
     - Returns immediately with `task_id` for async tracking
     - Handles timeout gracefully (expected for long tasks)
     - Provides clear feedback to LLM about task status

### 3. **Demo Script**
   - **File**: `scripts/demo_builder.py` (NEW)
   - **Purpose**: Demonstrate end-to-end invocation
   - **Flow**:
     1. Check if Builder is running (health check)
     2. Send A2A `message/send` request
     3. Show returned task_id
     4. Demonstrate async processing

### 4. **Integration Documentation**
   - **File**: `docs/builder_INTEGRATION.md` (NEW)
   - **Content**:
     - Architecture diagrams
     - Configuration guide
     - Usage examples (Python + direct HTTP)
     - Troubleshooting
     - Advanced topics (async timeout, status updates)

### 5. **Unit Tests**
   - **File**: `tests/progenitor/tools/test_invoke_builder.py` (NEW)
   - **Covers**:
     - Tool properties and metadata
     - Error handling (missing request, builder not running)
     - Async timeout handling
     - A2A request format validation
     - Auto-discovery verification

---

## 🔄 How It Works

### User Request Flow

```
User: "Create a comprehensive README for Builder"
  ↓
Progenitor LLM
  ├─ Analyzes: "This is a code generation task"
  ├─ Matches: "build" capability → Builder
  └─ Selects: invoke_builder system tool
    ↓
invoke_builder Tool
  ├─ Creates: A2A message/send request
  ├─ Posts: POST http://localhost:8108/
  └─ Gets: {"timeout": "expected", "task_id": "copilot_abc123..."}
    ↓
Returns to User
  └─ "Task accepted (ID: copilot_abc123). Processing asynchronously..."
    
Meanwhile, Builder (Background)
  ├─ Receives: A2A request (HTTP POST)
  ├─ Accepts: Returns 200 OK immediately
  ├─ Executes: plan → build → test → lint → pr workflow
  ├─ Streams: TaskStatusUpdateEvent updates
  └─ Completes: Creates pull request
```

### A2A Protocol Details

**Request Format:**
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
      "parts": [{"kind": "text", "text": "request..."}]
    },
    "metadata": {}
  }
}
```

**Response:**
- **Immediate**: 200 OK (even if task takes hours)
- **Async**: Builder processes in background
- **Streams**: Status updates via TaskStatusUpdateEvent
- **Returns**: Result via TaskArtifactUpdateEvent

---

## 📂 Files Changed / Created

### Created (5 files):
```
✅ data/config/agents.json
   └─ Agent registry configuration

✅ app/progenitor/tools/invoke_builder.py
   └─ System tool for invoking Builder

✅ scripts/demo_builder.py
   └─ Demo/test script

✅ docs/builder_INTEGRATION.md
   └─ Integration documentation

✅ tests/progenitor/tools/test_invoke_builder.py
   └─ Unit tests
```

### No Changes Needed:
- ✅ `app/progenitor/tools/mcp.py` - Already has correct `message/send` format
- ✅ `app/progenitor/nodes/node_helpers/registry.py` - Auto-discovers tools
- ✅ `app/agents/builder/agent.py` - Already implements A2A correctly
- ✅ `app/agents/builder/agent.py` - Executor already streams TaskStatusUpdateEvent

---

## 🚀 Quick Start

### 1. Start Builder
```bash
python -m uvicorn app.agents.builder.agent:app --port 8108
```

### 2. Test Integration
```bash
python scripts/demo_builder.py
```

Expected output:
```
[OK] Builder is running
[*] Sending A2A message/send to Builder...
[OK] Task accepted!
    Task ID: copilot_db363d980bca
```

### 3. Use in Progenitor
```bash
python -m app.progenitor.main
```

Then ask:
```
User: "Create a comprehensive README for Builder"
Progenitor: "Task accepted (ID: copilot_abc123). Processing asynchronously..."
```

---

## 🔑 Key Design Decisions

### 1. **Async-First**
- Builder accepts requests immediately
- Returns `task_id` for monitoring
- No blocking on HTTP request (timeout expected after 30s)
- ✅ Matches real-world agent behavior

### 2. **A2A Protocol**
- Uses standard JSON-RPC 2.0 `message/send`
- Same format as other agents (Builder Agent, Observe Agent)
- Compatible with a2a-sdk
- ✅ Official A2A standard, not custom protocol

### 3. **Auto-Discovery**
- Tool is auto-discovered by `registry.py`
- No manual registration needed
- Agents loaded from `data/config/agents.json` on startup
- ✅ Scales with new agents automatically

### 4. **Error Handling**
- Timeout is **expected** (not an error)
- Task continues in background
- Clear return value indicates success
- ✅ Prevents confusion about "failed" vs "still processing"

---

## 📊 Architecture Diagram

```
Progenitor (Main Loop)
├── State: Current messages, pending work
├── Decide: Route to agent or tool
├── LLM: "Which tool?"
├── Response: "invoke_builder"
└── System Tools
    ├── list_agents
    ├── list_tasks
    ├── new_conversation
    ├── invoke_builder ← NEW
    └── ...

invoke_builder Tool
├── Receives: user_input + tool_args
├── Validates: Non-empty request text
├── Prepares: A2A message/send JSON-RPC request
├── Sends: POST http://localhost:8108/
├── Handles: Timeout/error gracefully
└── Returns: task_id + status

Builder Agent (HTTP Server)
├── Port: 8108 (configurable)
├── Protocol: A2A JSON-RPC 2.0
├── Endpoint: POST /
├── Workflow: plan → build → test → lint → pr
└── Events: Streams TaskStatusUpdateEvent updates
```

---

## ✨ Integration Benefits

| Aspect | Benefit |
|--------|---------|
| **Async** | Don't wait for PR creation (can take 5+ min) |
| **Scalable** | Works with any A2A-compatible agent |
| **Standard** | Uses official A2A protocol, not custom |
| **Observable** | Clear task_id for monitoring |
| **Resilient** | Timeout is expected, not an error |
| **Extensible** | Easy to add more agents/tools |

---

## 🧪 Testing

### Run Demo
```bash
python scripts/demo_builder.py
```

### Run Unit Tests
```bash
pytest tests/progenitor/tools/test_invoke_builder.py -v
```

### Manual Integration Test
```python
# In Progenitor shell or as user request:
User: "Create a comprehensive README for Builder"
# Should invoke tool automatically
```

---

## 📝 Configuration Files

### `data/config/agents.json`
Registers agents for Progenitor to discover:

```json
{
  "builder": {
    "kind": "a2a_agent",
    "version": "0.1.0",
    "url": "http://localhost:8108",
    "capabilities": ["build", "plan", "test", "lint", "pr", "async_tasks"]
  }
}
```

---

## 🔗 Related Documents

- **Integration Guide**: `docs/builder_INTEGRATION.md`
- **Demo Script**: `scripts/demo_builder.py`
- **Tests**: `tests/progenitor/tools/test_invoke_builder.py`
- **Agent Code**: `app/agents/builder/agent.py`
- **Tool Code**: `app/progenitor/tools/invoke_builder.py`

---

## ✅ Verification Checklist

- [x] Builder runs successfully on port 8108
- [x] A2A `message/send` requests are accepted
- [x] Responses return immediately with 200 OK
- [x] Tasks execute asynchronously in background
- [x] Task IDs are returned for monitoring
- [x] Timeout is handled gracefully (expected for async)
- [x] Demo script works end-to-end
- [x] Tool is auto-discovered by Progenitor registry
- [x] Tool can be invoked from LLM context
- [x] Agent configuration loaded on startup
- [x] Unit tests pass (basic coverage)
- [x] Documentation complete

---

## 🎉 Result

**Progenitor can now invoke Builder asynchronously for code generation tasks.**

The integration uses the A2A protocol (JSON-RPC 2.0), ensuring:
1. **Immediate response** (no blocking on long tasks)
2. **Async execution** (Builder works in background)
3. **Standard protocol** (works with any A2A-compatible agent)
4. **Scalability** (easy to add more agents)

Example:
```
User: "Create a README for Builder"
→ Progenitor (LLM decides to delegate)
→ invoke_builder tool (sends A2A request)
→ Builder (accepts, processes asynchronously)
→ Result: Pull request created on GitHub
```

