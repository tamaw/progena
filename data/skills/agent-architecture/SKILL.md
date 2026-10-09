---
name: agent-architecture
description: Understand Progenitor's dual-loop architecture, state management, A2A protocol, and core system design. Use when debugging agent behavior, extending the core runtime, or explaining how Progenitor works.
---

# Agent Architecture Skill

## When to Use
- Questions about how Progenitor works
- Debugging agent behavior or state issues
- Extending the core system
- Understanding the dual-loop pattern

## Dual-Loop Architecture

### Outer Loop (Perpetual)
```
Intake → TimeCheck → EnqueueDue → Decide → [InnerLoop] → Respond → Idle → repeat
```
- Processes user input and scheduled tasks
- Routes to appropriate action (answer directly, build, delegate, schedule)
- Backs off exponentially when idle

### Inner Loop (Build Iteration)
```
OBSERVE → THINK → PLAN → BUILD → MERGE → EXECUTE → VERIFY → LEARN
```
- 8-phase iterative refinement for code generation
- Anti-criteria learning and weighted verification scoring
- Parallel git worktrees for isolation
- LLM-based merge conflict resolution

## State Persistence
Runtime state and structured logs are stored in the LangGraph-backed Postgres
store, not as JSON files under `data/state` or `data/config`.

The runtime state namespace is `("progenitor", "state")`; structured logs use
`("progenitor", "logs", <source>)`. The agent receives validated virtual-file
views backed by those documents:

- `/config/mcp.json` — MCP server configuration.
- `/config/knowledge.json` — knowledge-base settings.
- `/config/schedule.json` — scheduled tasks keyed by ID.
- `/config/agents.json` — A2A registry agents and runtime tools.
- `/config/preferences.json` — user profile and UI settings (display name, timezone, theme, nav/panel layout).
- `/config/health.json` — read-only heartbeat status.
- `/config/repos.json` — declared repositories and their local paths.
- `/config/repo_scan.json` — read-only latest repository scan, written by the heartbeat.
- `/config/profile.json` — named model profiles (provider/deployment and reasoning settings).
- `/config/plugins.json` and `/config/marketplaces.json` — installed plugins and configured marketplaces.
- `/tasks/board.json` and `/tasks/<id>.md` — kanban structure and cards.
- `/workflows/<id>.json` — workflow definitions.
- `/routines/<id>.json` — routine graph definitions.
- `/policies/<id>.json` — validated routine execution policies.
- `/agents/` (read-only) and `/agents/user/<name>/SYSTEM.md` (writable) — object-agent manifests.
- `/memories/agents/<name>/AGENTS.md` — private memory for object agents with `memory: true`; excluded from Progenitor's own memory.

Use the native `read_file`, `write_file`, and `edit_file` tools on those virtual
paths. The state backend validates the resulting document and persists it
through `StateManager`; do not edit a local JSON file or invent a `data/config`
path. Conversation messages are stored in the LangGraph checkpoint, while
per-thread `current_state` documents hold scalar bookkeeping.

## A2A Protocol
Agents communicate via HTTP:
- Discovery: `GET /.well-known/agent.json`
- Health: `GET /health`
- Task: `POST /a2a` with `{"message": "...", "context": {...}}`
- Port range: 8100-8199

## Key Directories
- `app/progenitor/`: Core agent logic
- `app/progenitor/nodes/`: LangGraph node implementations
- `app/progenitor/state/`: State models and persistence
- `app/progenitor/tools/`: System tools (23+)
- `app/progenitor/runtime/`: Loop execution
- `app/progenitor/sse/`: HTTP+SSE stream envelope helpers
- `app/agents/`: A2A agent implementations
- `app/mcp/`: MCP tool implementations

## Configuration
Environment variables take precedence over JSON config:
- `PROGENITOR_LLM_PROVIDER`: azure | openai
- `AZURE_OPENAI_BASE_URL`: Azure OpenAI base URL (without `/responses`)
- `AZURE_OPENAI_API_KEY`: Azure OpenAI API key
- `PROGENITOR_MODEL_DEFAULT`: Default model name
- `PROGENITOR_MOCK_LLM`: Enable mock mode for testing
