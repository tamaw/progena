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
- `PROGENITOR_DATA_DIR`: Data directory path

#### Config Files
- `current_state.json`: User session state
- `a2a_tasks.json`: Tracked external A2A tasks
- `data/config/schedule.json`: Scheduled tasks

### Key Patterns

#### State Persistence
```python
# Load state
state = await state_manager.load_state()

# Update state
await state_manager.update_state({"key": "value"})

# Save explicitly
await state_manager.save_state()
```

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
- State persists automatically between runs
- Exponential backoff when idle
- Interrupt-safe (graceful shutdown on Ctrl+C)
- Secret redaction in logs

### References
- Runtime Spec: `docs/SPEC.md`
- Architecture: `docs/ARCHITECTURE_DIAGRAM.md`
- State Models: `app/progenitor/state/models.py`
- Graph Definitions: `app/progenitor/graph/progenitor_graph.py`, `app/progenitor/graph/heartbeat_graph.py`
