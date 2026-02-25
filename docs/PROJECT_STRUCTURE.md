# Progenitor Project Structure

This document reflects the current repository layout and the major runtime components.

## Root Layout

```
progenitor/
├── app/                         # Runtime applications (agent, backend, frontend)
├── data/                        # Persistent runtime data (state, sessions, attachments)
├── docker/                      # Docker compose files and runner Dockerfiles
├── docs/                        # Project documentation
├── prompts/                     # Prompt templates used by the agent
│   └── skills/                  # DeepAgent SKILL.md files (progressive disclosure)
│       ├── build-agent/         # A2A agent and MCP tool building patterns
│       ├── scheduling/          # Task scheduling skill
│       ├── git-workflow/        # Git operations and worktree management
│       ├── self-improvement/    # Iteration analysis, pattern extraction
│       └── agent-architecture/  # System architecture reference
├── scripts/                     # Utility and demo scripts
├── tests/                       # Test suite
├── worktrees/                   # Worktree artifacts for build flows (builder_agent)
├── logs/                        # Runtime logs
├── pyproject.toml
└── requirements.txt
```

## Application Code

```
app/
├── progenitor/                  # Core perpetual agent (chat + delegation)
│   ├── main.py                  # Service entrypoint
│   ├── routing.py               # Graph routing helpers
│   ├── agent/                   # DeepAgent integration layer
│   │   ├── deep_agent.py        # Main factory: create_progenitor_agent()
│   │   ├── langchain_adapter.py # ChatOpenAI → LiteLLM proxy bridge
│   │   ├── tools_adapter.py     # BaseSystemTool → DeepAgent tool wrappers
│   │   ├── backends.py          # CompositeBackend (LocalShell + Store + State)
│   │   ├── middleware.py        # SecretRedaction, AuditLogging, Metrics
│   │   └── subagents.py         # A2A subagent wrappers (builder, reviewer)
│   ├── nodes/                   # Outer loop nodes: intake, decide, chat, respond,
│   │                            #   idle, pick_next, register, schedule_dispatch
│   ├── state/                   # State models (ProgenitorState, WorkItem, etc.)
│   ├── llm/                     # LLM client + prompt loading + mock client
│   ├── tools/                   # Tool registry + integrations (schedule, MCP, registry,
│   │                            #   self_reflect, shell, gh, agent_discovery, etc.)
│   ├── runtime/                 # Loop runner, scheduler, HTTP agent manager,
│   │                            #   DeepAgent adapter
│   └── ipc/                     # IPC protocol/server/client
│
├── agents/                      # Self-contained A2A agents
│   ├── builder_agent/           # Build loop agent (A2A, port 8107)
│   │   ├── agent.py             # A2A server entry point (BuilderAgentExecutor)
│   │   ├── graph.py             # Inner loop graph: OBSERVE→THINK→PLAN→BUILD→
│   │   │                        #   EXECUTE→VERIFY→LEARN (SQLite checkpointing)
│   │   ├── routing.py           # Graph routing helpers
│   │   ├── Dockerfile           # Self-contained: git + gh CLI + Copilot CLI
│   │   ├── requirements.txt     # Isolated dependencies (a2a-sdk, langgraph, etc.)
│   │   ├── nodes/               # Inner loop node implementations
│   │   ├── state/               # Build state models
│   │   ├── llm/                 # LLM client + prompts
│   │   ├── tools/               # Build tools (git, copilot, registry, shell, etc.)
│   │   └── runtime/             # BackgroundBuildRunner, CopilotACPManager, etc.
│   └── observe_agent/           # Observation/monitoring A2A agent
│
├── backend/                     # FastAPI OpenAI-compatible API (:8000)
├── frontend/                    # React + Vite web client (:5173)
└── mcp/                         # MCP servers
```

## Tests

```
tests/
├── progenitor/                  # Tests for core progenitor agent
├── agents/
│   ├── builder_agent/           # Tests for builder_agent (AgentCard, executor, graph)
│   └── test_observe_agent.py
└── backend/                     # Tests for FastAPI backend
```

## Runtime Data

```
data/
├── state/                       # Core state files (json)
│   ├── current_state.json       # Progenitor: conversation + messages
│   ├── registry.json            # Agent/tool registry
│   ├── schedule.json            # Scheduled tasks
│   └── build_checkpoints.db    # Builder agent: SQLite LangGraph checkpoints
├── sessions/                    # Session persistence for backend/UI
├── attachments/                 # Uploaded file storage/cache
└── memory/                      # Additional memory artifacts
```

## Docker Assets

```
docker/
├── docker-compose.progenitor.yml  # Main stack (litellm + progenitor + backend)
├── docker-compose.litellm.yml     # LiteLLM-focused stack
└── runner/
    ├── Dockerfile.agent           # Generic runner image for A2A agent processes
    └── Dockerfile.mcp             # Runner image for MCP processes

# Per-agent Dockerfiles (self-contained):
app/agents/builder_agent/Dockerfile   # Includes git, gh CLI, GitHub Copilot CLI
```

## Common Development Commands

```bash
# Activate virtual environment first
& .\.venv\Scripts\Activate.ps1  # Windows
source .venv/bin/activate        # Linux/Mac

# Run all tests
pytest

# Run only builder agent tests
pytest tests/agents/builder_agent/ -v

# Type check
mypy app/

# Lint
ruff check app/
```

## Design Principles

- **Progenitor** is a stable, minimal orchestrator: chat, routing, delegation
- **Builder Agent** owns all risky capabilities: code generation, git mutation, shell execution
- Each agent is self-contained with its own Dockerfile, requirements.txt, and state
- Communication between agents uses the A2A protocol (JSON-RPC over HTTP)
- CI pipeline matrix-builds each agent's Dockerfile independently
