---
name: agent-architecture
description: Understanding and working with Progenitor's dual-loop architecture, state management, A2A protocol, and system design
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
- `current_state.json`: Conversation state (messages, tool calls)
- `build_state.json`: Build backlog, work items, verification results
- `schedule.json`: Scheduled tasks
- `registry.json`: Agent/tool registry

All state writes are atomic: write to temp file → fsync → rename.

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
- `app/progenitor/ipc/`: Inter-process communication
- `app/agents/`: A2A agent implementations
- `app/mcp/`: MCP tool implementations

## Configuration
Environment variables take precedence over JSON config:
- `PROGENITOR_LLM_PROVIDER`: azure | openai
- `LITELLM_BASE_URL`: LiteLLM proxy URL
- `PROGENITOR_MODEL_DEFAULT`: Default model name
- `PROGENITOR_MOCK_LLM`: Enable mock mode for testing
