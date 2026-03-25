# Progenitor Advanced Agent Features

This document describes the advanced capabilities available to agents in the Progenitor ecosystem. Each feature is documented at a high level: what it does, where it lives in the codebase, and how to use it. Implementation details and code examples are kept concise so the document is useful as a quick reference for both human developers and AI agents.

> **Scope:** Features in this document go beyond the basic A2A contract described in `docs/AGENT_SPEC.md`. Most apply only to the main Progenitor agent or to DeepAgent-based agents. Per-feature scope is noted.

---

## Table of Contents

1. [Scheduling](#1-scheduling)
2. [Skills (DeepAgent only)](#2-skills-deepagent-only)
3. [Virtual Filesystem (DeepAgent only)](#3-virtual-filesystem-deepagent-only)
4. [Sub-Agents](#4-sub-agents)
5. [Human-in-the-Loop (Clarification)](#5-human-in-the-loop-clarification)
6. [Memory (DeepAgent only)](#6-memory-deepagent-only)

---

## 1. Scheduling

**Scope:** Main Progenitor loop and any agent that calls `schedule_task`.

### What it is

Scheduling lets Progenitor execute work at a future time. Both one-off and recurring tasks are supported. The user can express times in natural language ("in 30 minutes", "every day at 9am") or as an explicit ISO 8601 timestamp.

### How it works

1. **Creating a task** — The `schedule_task` system tool (or the `/tools/schedule` API route) takes the user's request, parses the time expression, converts it to a cron expression or an explicit `scheduled_at` timestamp, and writes a new entry to `data/schedule.json`.

2. **Checking what is due** — On every outer-loop cycle the `EnqueueDue` node calls `Scheduler.get_due_items()`. This compares each entry's `next_run` against the current UTC time and returns a sorted list of overdue items.

3. **Dispatching** — Due items are prepended with a `SCHEDULED:` prefix and inserted into the main input queue. The loop then processes them exactly like regular user messages.

4. **After execution** — One-off tasks (`scheduled_at` entries, or cron entries with a fixed day and month) are deleted from `schedule.json`. Recurring tasks have their `last_run` timestamp updated so the next `croniter` run picks the correct next time.

### Key files

| File | Role |
|---|---|
| `app/progenitor/runtime/scheduler.py` | `Scheduler` class — time parsing, due-item detection, cron iteration |
| `app/progenitor/nodes/schedule_dispatch.py` | LangGraph node — checks due items and enqueues them |
| `app/progenitor/tools/schedule.py` | `schedule_task` and `list_schedule` system tools |
| `data/schedule.json` | Persisted schedule state (runtime) |

### Schedule entry shape

```json
{
  "id": "sched_abc123",
  "message": "Run daily health report",
  "user_request": "remind me to check health every morning",
  "cron_expression": "0 9 * * *",
  "scheduled_at": null,
  "last_run": null,
  "created_at": "2026-01-01T00:00:00Z",
  "description": "Daily at 09:00"
}
```

- `cron_expression` drives recurring tasks (parsed by `croniter`).
- `scheduled_at` is an ISO datetime used for one-off tasks; takes precedence over `cron_expression`.
- A one-off task is detected when `scheduled_at` is set, **or** when the cron expression has a fixed day and month (e.g. `30 14 5 6 *`).

### Supported natural language patterns

| Expression | Resolved type |
|---|---|
| `"in 30 minutes"` | one-off |
| `"at 9am"` | one-off (today or tomorrow) |
| `"tomorrow at 3pm"` | one-off |
| `"2026-06-01T14:30:00Z"` | one-off (explicit ISO) |
| `"every day at 9am"` | recurring (daily cron) |
| `"every 2 hours"` | recurring (interval cron) |
| `"every monday at 10:00"` | recurring (weekly cron) |

### Adding scheduling to a new agent

Any agent whose task queue calls the Progenitor `schedule_task` tool inherits scheduling automatically. To support it natively:

1. Persist a `schedule.json` in the agent's state directory.
2. Instantiate `Scheduler` and call `get_due_items()` on each cycle.
3. Delete one-shot entries after dispatch; update `last_run` for recurring ones.

---

## 2. Skills (DeepAgent only)

**Scope:** Agents built with `create_deep_agent()` — currently Progenitor (via `create_progenitor_agent`) and `builder_progenitor`.

### What it is

Skills are Markdown files in `prompts/skills/` that inject focused, on-demand knowledge into the agent's context window. Each skill is a short document describing *when* to use a capability and *how* to use it. Skills implement a pattern called **progressive disclosure**: the agent loads only the skills relevant to the current request, keeping the context window lean.

### Skill file format

Every skill lives at `prompts/skills/{skill_name}/SKILL.md` and has two parts:

```markdown
---
name: skill-name
description: One-line description used for selection
allowed-tools: tool_one tool_two
---

# Skill Name

## When to Use
- Bullet points describing the trigger conditions

## How to do it
...actual content the agent reads...
```

The YAML front-matter is stripped before the content is injected into the prompt.

### Built-in skills

| Skill | Purpose |
|---|---|
| `agent-architecture` | Dual-loop architecture, state management, A2A protocol |
| `build-agent` | Building new A2A agents and MCP tools |
| `git-workflow` | Branch, commit, PR patterns |
| `prompt-maintenance` | Editing and creating prompt files |
| `repo-readonly` | Reading files without modifying the repo |
| `runtime-inspection` | Inspecting running containers and logs |
| `scheduling` | Creating and managing scheduled tasks |
| `self-improvement` | Modifying Progenitor's own code safely |

### How DeepAgent loads skills

When `create_progenitor_agent()` is called, the `skills_dirs` parameter points to `prompts/skills/`. The `deepagents` library scans that directory at runtime and makes each `SKILL.md` available for selective injection. The agent sees all skill names and descriptions upfront; full content is only loaded when the skill is selected for the current request.

```python
# Auto-discovered in create_progenitor_agent():
skills_path = project_root / "prompts" / "skills"
skills_dirs = [str(skills_path)]   # passed to create_deep_agent(skills=...)
```

### How `builder_progenitor` uses skills

`builder_progenitor` uses `load_skill_context()` to embed selected skills directly in the execution prompt template:

```python
# prompts/builder_progenitor/execution_template.md receives:
skill_context = load_skill_context(["build-agent", "git-workflow"])
```

This produces a `## Repository Skills` section in the prompt with the stripped Markdown content of each selected skill.

### Adding a new skill

1. Create `prompts/skills/{name}/SKILL.md`.
2. Add the YAML front-matter with `name`, `description`, and `allowed-tools`.
3. Write **When to Use** and the how-to content.
4. No code changes are needed — `create_progenitor_agent()` auto-discovers new skill directories.

---

## 3. Virtual Filesystem (DeepAgent only)

**Scope:** Agents built with `create_progenitor_backend()` — currently Progenitor via `create_progenitor_agent`.

### What it is

The virtual filesystem (VFS) gives the DeepAgent structured, path-scoped access to host files without exposing the entire filesystem. Each mount point maps a virtual path to a real directory on disk with an explicit read/write permission.

### How it works

`create_progenitor_backend()` in `app/progenitor/agent/backends.py` builds a `CompositeBackend`. The composite routes VFS paths to individual `FilesystemBackend` instances. Every `FilesystemBackend` runs in `virtual_mode=True`, meaning file operations go through the DeepAgent abstraction rather than raw `open()` calls.

Read-only mounts are wrapped in `ReadOnlyFilesystemBackend`, which overrides `can_write_file()` and `can_delete_file()` to always return `False`.

### Default mount table (Progenitor)

| Virtual path | Real path | Permission |
|---|---|---|
| `/self/` | `<project_root>/app` | read-only |
| `/data/` | `<project_root>/data` | read-only |
| `/logs/` | `<project_root>/logs` | read-only |
| `/prompts/` | `<project_root>/prompts` | read-only |
| `/memories/` | LangGraph `BaseStore` (cross-thread) | read/write |

The default backend (for paths not matching any mount) is `StateBackend`, which routes through the LangGraph state object.

### `builder_progenitor` VFS

`builder_progenitor` uses `LocalShellBackend` from `deepagents.backends` instead:

```python
backend = LocalShellBackend(
    root_dir=project_root,
    virtual_mode=False,   # real filesystem access in a git worktree
    inherit_env=True,
    timeout=config.copilot.timeout_seconds,
)
```

`virtual_mode=False` gives direct disk access, which is needed to run shell commands in git worktrees. The isolation is provided by the worktree boundary rather than the virtual filesystem.

### Adding a VFS mount

To add a new read-only mount to Progenitor's backend, edit `create_progenitor_backend()`:

```python
mounts: list[tuple[str, Path, bool]] = [
    ("/self/",    root / "app",     True),
    ("/data/",    root / "data",    True),
    ("/logs/",    root / "logs",    True),
    ("/prompts/", root / "prompts", True),
    ("/mydir/",   root / "mydir",   True),   # new
]
```

The boolean is `True` for read-only, `False` for read/write.

---

## 4. Sub-Agents

**Scope:** Main Progenitor agent and any agent with a `ToolRegistry`.

### What it is

Sub-agents are independent A2A agents that Progenitor can discover and call as tools. From Progenitor's perspective they appear identical to system tools, but they run as separate processes (usually separate Docker containers) and communicate over HTTP using the A2A protocol.

### How it works

#### Discovery

On startup, Progenitor scans `app/agents/` using `AgentDiscovery`. For each subdirectory it reads the source files and uses an LLM with structured output to extract:

- `name`, `description`, `capabilities`
- `entrypoint` (the main Python file)
- `transport` (always `http`)
- `version`

Discovered agents are written to `data/config/agents.json`. The registry can also be refreshed at runtime via the `rediscover_agents` system tool.

#### Registration

```json
{
  "agents": [
    {
      "name": "observe_agent",
      "description": "Monitors Progenitor by reading mounted state/logs",
      "capabilities": ["progenitor_health_report", "diagnose_last_error"],
      "transport": "http",
      "url": "http://localhost:8080",
      "version": "0.1.0",
      "enabled": true
    }
  ]
}
```

#### Invocation

Progenitor calls sub-agents via the `call_agent` system tool, which:

1. Looks up the agent by `name` or `capability` in `agents.json`.
2. Fetches the agent's card from `GET /.well-known/agent.json` to confirm it is alive and get its current URL.
3. Sends a `message/send` JSON-RPC 2.0 request to `POST /`.
4. Returns the response text as a `ToolResult`.

The `chat` node also surfaces all registered agents as individual LLM tool definitions, so the LLM can address them by name directly:

```python
tools = _get_a2a_agent_tool_definitions(tool_registry)
# → [{"type": "function", "function": {"name": "observe_agent", ...}}, ...]
```

#### Key files

| File | Role |
|---|---|
| `app/progenitor/tools/agent_discovery.py` | Scans `app/agents/`, LLM analysis, writes `agents.json` |
| `app/progenitor/tools/call_agent.py` | `call_agent` system tool |
| `app/progenitor/tools/mcp.py` | `A2AClient` — HTTP transport for A2A calls |
| `data/config/agents.json` | Runtime registry |
| `app/progenitor/tools/registry.py` | `ToolRegistry` — in-memory index with capability lookup |

### Writing a sub-agent

Any agent that follows the contract in `docs/AGENT_SPEC.md` (health endpoint, agent card, A2A executor) will be discovered and callable automatically after `rediscover_agents` runs.

Ensure:
- `AGENT_ID` matches the directory name.
- The `AgentCard.capabilities` list is accurate — Progenitor routes by capability.
- The agent is running and reachable on the configured URL before `rediscover_agents` is called.

---

## 5. Human-in-the-Loop (Clarification)

**Scope:** Main Progenitor loop (`nodes/intake.py`, `nodes/respond.py`) and `builder_agent` (inner build loop, `state/clarification.py`).

### What it is

Human-in-the-loop (HITL) is the mechanism by which the agent pauses mid-task, sends a question to the user, waits for a response, and then resumes with the answer incorporated into its context. This happens automatically when the agent or the Copilot CLI determines it does not have enough information to proceed safely.

### How it works (Progenitor outer loop)

1. **Detection** — During the `VERIFY` phase of the inner loop, or when the `decide` node encounters an under-specified request, the system sets:
   - `clarification.needed = True`
   - `clarification_question = "Your question here"`
   - `clarification_context = {…context about what prompted the question…}`

2. **Respond node** — When `respond_node` sees `clarification.needed`, it:
   - Sets `awaiting_clarification = True` in state (persisted to `current_state.json`).
   - Logs the question as a `clarification_request` message.
   - Returns the question text as the pending response to the user.

3. **Intake node** — The next message from the user is tagged as a `clarification_response` and the `clarification_response` field is set in state.

4. **Observe node** — Clears `awaiting_clarification` and incorporates the answer before the agent continues.

### How it works (builder_agent inner loop)

`builder_agent` uses `ClarificationDetector` to analyse Copilot CLI output for signs that more information is needed (e.g. the CLI asked a question, output was ambiguous, or a required parameter was missing).

1. **`ClarificationDetector.detect(output)`** — Sends the output to the LLM with a structured-output prompt. Returns a `ClarificationRequest` with:
   - `needs_clarification: bool`
   - `question: str` (formatted for the user)
   - `category: str` — one of `requirements`, `technical`, `design`, `scope`, `other`
   - `confidence: float` (0–1)

2. **Verify node** — If `needs_clarification` is True (above a confidence threshold), the build is paused and the question is surfaced through the same outer-loop mechanism described above.

3. **Resume** — When the user responds, `IdealStateBuilder.refine_with_clarification()` re-runs the ideal-state extraction incorporating the new information, and the build loop continues from where it left off.

### State fields

| Field | Type | Meaning |
|---|---|---|
| `awaiting_clarification` | `bool` | True while the agent is waiting for the user's reply |
| `clarification_response` | `str \| None` | The user's answer (set by intake, consumed by observe) |
| `clarification_question` | `str` | The question surfaced to the user |
| `clarification_context` | `dict` | What triggered the clarification and the context |
| `clarification.needed` | `bool` | Signal from inner loop that a question is needed |

### Key files

| File | Role |
|---|---|
| `app/progenitor/nodes/intake.py` | Detects clarification responses; tags them in state |
| `app/progenitor/nodes/respond.py` | Detects clarification requests; pauses loop and asks user |
| `app/agents/builder_agent/state/clarification.py` | `ClarificationDetector` — LLM-based output analysis |
| `app/agents/builder_agent/state/ideal_state.py` | `IdealStateBuilder.refine_with_clarification()` |

### Design principle

Clarification is a loop pause, not a branch. The agent does not fork into a new conversation thread; it simply sets a flag and waits for the next user message. This keeps the state machine simple: all input — whether scheduled, user-initiated, or clarification-response — flows through the same `intake → decide` path.

---

## 6. Memory (DeepAgent only)

**Scope:** Agents created with `create_deep_agent()` that pass `store=` and/or `memory=` parameters.

### What it is

Memory is how the agent retains information across separate conversation sessions. There are two orthogonal layers:

| Layer | Mechanism | Scope | Persists? |
|---|---|---|---|
| **Conversation checkpoints** | SQLite via LangGraph checkpointer | Per thread/session | Yes — `data/state/conversations.db` |
| **Cross-thread store** | LangGraph `BaseStore` → `StoreBackend` | Across all threads | Yes in production (backing store); in-memory in dev |
| **Static memory files** | Markdown files loaded at startup | Global (read-only) | Yes — files on disk |

### Conversation checkpoints

Each conversation has a `thread_id`. LangGraph's `SqliteSaver` (production) or `MemorySaver` (tests/dev) records every graph state after each node runs. If the process restarts, the next invocation for the same `thread_id` resumes from the last checkpoint.

```python
# Default in create_progenitor_agent():
db_path = config.storage.state_dir / "conversations.db"
checkpointer = SqliteSaver.from_conn_string(str(db_path))
```

### Cross-thread store (`/memories/`)

The `StoreBackend` exposes the LangGraph `BaseStore` as a virtual filesystem mount at `/memories/`. The agent can read and write named memory slots that survive across different `thread_id` values. This is the correct place to store facts that should persist globally (e.g. user preferences, long-term goals, learned patterns).

In production, `InMemoryStore` can be swapped for a persistent implementation (e.g. a PostgreSQL-backed store) by passing a different `store=` to `create_progenitor_agent()`.

```python
# Dev default (lost on restart):
store = InMemoryStore()

# Production (persistent):
from langgraph.store.postgres import AsyncPostgresStore
store = AsyncPostgresStore(connection_string=...)
```

### Static memory files

Memory files are plain Markdown (or any text) files that are injected into the agent's system context at startup. They are useful for:

- Repository-level conventions (`AGENTS.md`)
- Standing instructions ("always use British English")
- Long-lived facts ("the production database is read-only on weekends")

`create_progenitor_agent()` auto-discovers `AGENTS.md` in the project root:

```python
agents_md = project_root / "AGENTS.md"
if agents_md.is_file():
    memory_paths.append(str(agents_md))
```

Additional files can be passed via `memory_paths=[...]`. The contents are made available to the DeepAgent at startup as static context.

### LLM audit log and conversation log

Separately from the above, every conversation message is appended to JSONL logs:

- `data/logs/conversation.jsonl` — all user and assistant messages.
- `{PROGENITOR_LOG_DIR}/{AGENT_NAME}/llm_requests.jsonl` — every LLM API call with timing.

These are audit trails, not queryable memory, but they can be read by `observe_agent` or searched by the runtime-inspection skill.

### Summary: what to use for what

| Use case | Mechanism |
|---|---|
| Resume a conversation after restart | SQLite checkpointer (`conversations.db`) |
| Share a fact across conversations | Cross-thread store (`/memories/` VFS) |
| Inject standing instructions at boot | Static memory file (e.g. `AGENTS.md`) |
| Audit LLM calls | `llm_requests.jsonl` |
| Replay a conversation for debugging | `conversation.jsonl` |

### Key files

| File | Role |
|---|---|
| `app/progenitor/agent/deep_agent.py` | Wires checkpointer, store, and memory paths into `create_deep_agent` |
| `app/progenitor/agent/backends.py` | `StoreBackend` at `/memories/` inside `CompositeBackend` |
| `data/state/conversations.db` | SQLite conversation checkpoint store (runtime) |
| `AGENTS.md` | Default static memory file injected at startup |
