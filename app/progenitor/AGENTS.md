# Progenitor - GitHub Copilot Agent

## DeepAgent Orchestrator Loop

The core perpetual agent is a conversational orchestrator. It hosts the
DeepAgent chat surface, maintains durable state, runs schedules, and delegates
specialized work to external A2A agents.

### Agent Capabilities
- LangGraph state machine design and implementation
- Async Python patterns and coroutines
- State management and persistence
- LLM integration (Azure OpenAI, OpenAI)
- Agent routing and orchestration
- A2A (Agent-to-Agent) communication
- Scheduled task execution

### Architecture

#### Dual-Loop Pattern
**User Loop:**
```
Intake → DeepAgent Chat → Respond → Idle → repeat
```

**Background Loop:**
```
PickDueSchedule → Intake → DeepAgent Chat → Respond → AdvanceSchedule
A2ACallback → RecordTaskUpdate → Notify
```

#### Directory Structure
```
progenitor/
├── config.py            # Configuration dataclasses
├── graph/               # Graph builders
│   ├── progenitor_graph.py  # Conversational DeepAgent graph
│   └── heartbeat_graph.py   # Background heartbeat/schedule/A2A graph
├── main.py              # Entry point
├── nodes/               # LangGraph node implementations
│   ├── chat.py          # Main LLM tool-calling node
│   ├── intake.py        # Message intake node
│   ├── a2a.py           # A2A task/callback updates
│   ├── schedule.py      # Scheduled work dispatch
│   ├── respond.py       # Response generation node
│   └── ...
├── runtime/
│   ├── progenitor.py    # Main Progenitor runtime lifecycle
│   └── heartbeat.py     # Perpetual heartbeat/background loop execution
├── state/
│   ├── models.py        # Pydantic state models
│   └── manager.py       # State persistence manager
└── llm/
    └── client.py        # LLM client abstraction
```

### Key Components

#### State Models (`state/models.py`)
- `ProgenitorState`: TypedDict for LangGraph nodes
- `ConversationMessage`: persisted user/assistant/tool messages
- `ScheduleEntry`: durable scheduled tasks
- `A2ATaskRecord`: tracked external A2A task state

#### Node Pattern
All nodes follow this factory pattern:
```python
def create_node(state_manager, llm_client=None) -> Callable[[ProgenitorState], Awaitable[dict]]:
    async def node(state: ProgenitorState) -> dict:
        # Node logic - return partial state update
        return {"key": "value"}
    return node
```

Builder, worktree, Copilot CLI, and PR workflows belong to external builder
agents. Progenitor can call those agents through `call_agent`, but it does not
own their implementation loop.

### Development

#### Running the Agent
```bash
# Activate venv
& .\.venv\Scripts\Activate.ps1

# Run directly
python -m app.progenitor.main

# With mock LLM (for testing)
$env:PROGENITOR_MOCK_LLM = "true"
python -m app.progenitor.main
```

#### Testing
```bash
# Run all progenitor tests
pytest tests/progenitor/ -v

# Run specific test file
pytest tests/progenitor/test_nodes.py -v

# Run with coverage
pytest tests/progenitor/ --cov=app.progenitor
```

### Configuration

#### Environment Variables
- `PROGENITOR_LLM_PROVIDER`: `azure` | `openai`
- `AZURE_OPENAI_*`: Azure deployment settings
- `OPENAI_API_KEY`: OpenAI API key
- `PROGENITOR_MOCK_LLM`: Enable mock mode
- `PROGENITOR_DATABASE_URL`: PostgreSQL URL for the LangGraph checkpointer/store
- `PROGENITOR_DATA_DIR`: Read-only runtime data root for prompts, schemas, skills, and memories

#### Runtime Persistence and Virtual Files
The normal Progenitor runtime stores state and structured logs in the LangGraph
Postgres store. Runtime state uses the `("progenitor", "state")` namespace;
structured logs use `("progenitor", "logs", <source>)`. Progenitor does not
write its runtime state as JSON files under `data/state`.

The DeepAgent receives a virtual filesystem view backed by those documents:
- `/state/mcp.json`: MCP server configuration
- `/state/knowledge.json`: Knowledge-base settings
- `/state/schedule.json`: Scheduled tasks
- `/state/agents.json`: A2A agent registry and runtime tools
- `/state/health.json`: Read-only heartbeat status
- `/state/tasks/board.json` and `/state/tasks/<id>.md`: Kanban structure and cards
- `/state/workflows/<id>.json`: Workflow definitions

Native `read`/`write`/`edit` operations on these paths validate and persist the
corresponding Postgres document. Conversation messages are kept in the
LangGraph checkpoint; the per-thread `current_state` document stores scalar
bookkeeping such as cycle and response status. Names such as
`current_state.json` and `a2a_tasks.json` are legacy/document labels, not local
filesystem paths.

### Key Patterns

#### State Persistence
```python
# Read a runtime document
state = await state_manager.get_current_state(thread_id=thread_id)

# Merge scalar bookkeeping into the Postgres-backed document
await state_manager.update_current_state({"pending_response": response}, thread_id=thread_id)

# Read or update a virtual-file document through the same store
schedule = await state_manager.get_schedule()
await state_manager.save_schedule(schedule)
```

The standalone agent factory can use a local checkpointer fallback when no
checkpointer is injected, but the production `Progenitor` runtime injects the
Postgres checkpointer and store.

#### LLM Calls
```python
# Conversation graph
graph = create_conversation_graph(config=config)

# Heartbeat/background graph
background = create_heartbeat_graph(state_manager=state_manager)

# Direct prompt loading
prompt = load_prompt("progenitor/genesis.md")
```

#### Secret Redaction
`redact_secrets()` auto-redacts API keys in logs. Patterns cover OpenAI, Azure, GitHub, Anthropic, and AWS keys.

### Testing Conventions
- Use `pytest-asyncio` with `asyncio_mode = auto`
- Mock LLM client: `mock_llm = AsyncMock(); mock_llm.decide.return_value = {...}`
- Mock state manager: `mock_state_manager = AsyncMock()`
- Example: `tests/progenitor/test_nodes.py`

### Important Notes
- Always activate venv before Python commands
- State persists automatically in the configured Postgres store
- Exponential backoff when idle
- Interrupt-safe (graceful shutdown on Ctrl+C)
- Secret redaction in logs

### References
- Runtime Spec: `docs/SPEC.md`
- State Documents: `docs/STATE_DOCUMENTS.md`
- Architecture: `docs/ARCHITECTURE_DIAGRAM.md`
- State Models: `app/progenitor/state/models.py`
- Graph Definitions: `app/progenitor/graph/progenitor_graph.py`, `app/progenitor/graph/heartbeat_graph.py`
