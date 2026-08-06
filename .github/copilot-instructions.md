# Progenitor - Copilot Instructions

## ⚠️ CRITICAL: Python Virtual Environment

**ALWAYS activate venv before running any Python commands:**
```powershell
& .\.venv\Scripts\Activate.ps1  # Windows
```
```bash
source .venv/bin/activate  # Linux/Mac
```

## Architecture Overview

Progenitor is a **dual-loop LangGraph AI agent** implementing the "Last Algorithm" pattern:

**Outer Loop (Perpetual):** `Intake → TimeCheck → EnqueueDue → Decide → [InnerLoop] → Respond → Idle → repeat`

**Inner Loop (Iteration):** `Observe → Think → Plan → Build → Execute → Validate → Verify → Learn`

The agent runs continuously, processing user input and scheduled tasks. When idle, it backs off exponentially.

### Key Components
- **Backend** (`app/backend/`): FastAPI server with OpenAI-compatible API at `:8000`
- **Frontend** (`app/frontend/`): React/Vite app at `:5173` (dev) - proxies API calls to backend
- **Agent** (`app/progenitor/`): The LangGraph loop orchestrating everything

### Data Flow
```
User → Frontend → Backend (/v1/chat) → HTTP + SSE → ProgenitorLoop → LangGraph nodes → LLM
                                    ↑↓ (async queues)
                        State persistence:
                        - current_state.json (user session: messages, tool calls)
                        - build_state.json (build state: backlog, verification)
                        - schedule document, Postgres-backed (system feature: scheduled tasks)
```

## Developer Workflow

| Task | Command |
|------|---------|
| Start all (dev) | VS Code Task: "Start All (dev)" or run each manually |
| Run tests | `pytest` (auto-discovers, uses `asyncio_mode = auto`) |
| Run progenitor tests | `pytest tests/progenitor/ -v` |
| Type check | `pyright app/` |
| Lint | `ruff check app/` |

**Mock mode** (no real LLM): `$env:PROGENITOR_MOCK_LLM = "true"`

## Project-Specific Patterns

### State Models (Pydantic v2)
All state models live in `app/progenitor/state/models.py`. Key types:
- `WorkItem`: Backlog items with `WorkItemType` (BUILD_AGENT, A2A_CALL, SYSTEM_TOOL, etc.)
- `Decision`: Routing decisions with `DecisionAction` (ANSWER_DIRECTLY, DELEGATE_A2A, BUILD_AGENT, etc.)
- `ProgenitorState`: TypedDict used by all LangGraph nodes

### Node Pattern
Nodes in `app/progenitor/nodes/` follow a factory pattern:
```python
def create_node(state_manager, llm_client=None) -> Callable[[ProgenitorState], Awaitable[dict]]:
    async def node(state: ProgenitorState) -> dict:
        # Node logic - return partial state update
        return {"key": "value"}
    return node
```

### Graph Routing
Routing functions in `app/progenitor/graph.py` determine flow:
- `should_continue()`: `pick_next` | `idle` | `end`
- `route_after_decide()`: `planner` | `pick_next` | `respond`
- `route_after_validate()`: `register` | `pick_next`

### Configuration Hierarchy
Environment variables > JSON config files. Key configs:
- `PROGENITOR_LLM_PROVIDER`: `azure` | `openai`
- `AZURE_OPENAI_*`: Azure deployment settings
- `PROGENITOR_MOCK_LLM`: Enable mock mode for testing

See `app/progenitor/config.py` for full dataclass definitions.

### Testing Conventions
- Tests use `pytest-asyncio` with `asyncio_mode = auto`
- Mock the LLM client: `mock_llm = AsyncMock(); mock_llm.decide.return_value = {...}`
- State managers are mocked: `mock_state_manager = AsyncMock()`
- Example: `tests/progenitor/test_nodes.py`

### Secret Redaction
`redact_secrets()` in `app/progenitor/state/models.py` auto-redacts API keys in logs. Patterns cover OpenAI, Azure, GitHub, Anthropic, and AWS keys.

## Key Files

- `app/progenitor/graph.py` - LangGraph topology and routing
- `app/progenitor/runtime/loop.py` - Perpetual loop execution
- `app/progenitor/state/models.py` - All Pydantic models
- `app/progenitor/config.py` - Configuration dataclasses
- `app/backend/main.py` - FastAPI endpoints
- `docs/SPEC.md` - Checked-in main system specification
- `docs/ARCHITECTURE_DIAGRAM.md` - Visual architecture
