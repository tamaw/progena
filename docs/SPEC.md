# Progenitor Agent – Specification

## Purpose

Progenitor is your **personal assistant** and bootstrap builder for the TARDIS system. It accepts *any* user input conversationally, decides whether to **answer directly**, **delegate to existing agents via A2A**, or **build/upgrade agents** (via Copilot CLI) to handle the request better next time.

It runs as a **perpetual loop**: **listen → decide → delegate/build → persist → respond → idle → repeat**.

The agent does not terminate on its own. When idle, it explicitly asks the user if there is anything to do.

---

## Core Principles

* **Conversational-first**: always-on personal assistant
* **Perpetual loop**: no natural termination; idles when no work exists
* **Decision hub**: answers directly, delegates via A2A, or builds agents as needed
* **Tool-first execution**: Copilot CLI builds; agents do work; Progenitor orchestrates
* **Explicit persistence**: all important state and conversation is written to disk
* **Everything allowed**: all commands are allowable; behaviour is governed by logging, not restriction

---

## High‑Level Responsibilities

1. Accept user messages (free‑form conversation)
2. Maintain full conversation history as primary context
3. Classify intent: answer | delegate (A2A) | schedule | build/upgrade agents/tools | mix
4. Maintain a backlog of work items **and scheduled tasks** in state
5. Execute work items via Copilot CLI (build/modify agents, tools, MCP servers)
6. Execute functional work via A2A calls to existing agents
7. Trigger scheduled tasks based on wall‑clock time
8. Persist core files with locks + append‑only JSONL logs
9. Respond to the user with outcomes and next actions
10. Continue perpetually, idling when no immediate work exists

---

## Architecture Overview

### Execution Model

Progenitor runs **perpetually** inside a Docker container as a continuous LangGraph loop.

The loop is intentionally **infinite**. When there’s nothing queued, it idles and prompts: *“Anything to do?”* (or waits on an input queue).

Controls still exist so it doesn’t spin at 100% CPU:

* idle sleep/backoff when no new input
* max actions per cycle (tool/A2A/Codex)
* restart-safe checkpoints (write state every cycle)
* optional user pause/stop (if you want it)

---

## Messaging Interface (WhatsApp-first)

Progenitor requires a real inbox, not just a terminal.

### Channel Gateway Pattern

Use a **channel gateway** (inspired by Clawdbot) that connects messaging platforms to the Progenitor runtime.

Flow:

* **Channel (WhatsApp)** → **Gateway/Ingest** → **Inbox queue** → **Progenitor Intake**
* **Progenitor Respond** → **Outbox queue** → **Gateway/Send** → **Channel**

The gateway normalises inbound messages and relays outbound responses. Progenitor itself remains transport-agnostic.

### Security / Ownership

* Default to sender allowlisting or pairing
* Single-user setups may run open, but the concept remains

### Integration with the Loop

* Intake consumes inbox events
* Respond emits outbox events
* Scheduled jobs also emit messages via the outbox

---

## Technology Stack

### Language

* Python 3.x

### Core Libraries

* **OpenAI API (Responses)** for all model calls
* **LangGraph** for loop orchestration and control flow
* **Pydantic v2** for strict schemas and state validation (core state only)

### Tooling

* **Copilot CLI** (primary builder actuator)
* Git with **worktrees** (one worktree per work item)
* **Docker** (runtime container)

### Transports

* **Backend (FastAPI)**: OpenAI-compatible REST API gateway (primary for development/testing)
* WhatsApp gateway (production)
* Pluggable transports (webhook, CLI, future Slack/Matrix)

---

## Configuration & Environment Variables

Keep it simple. Progenitor is configured via **environment variables** (preferred) with optional JSON config defaults. Environment variables always win.

### Provider

* `PROGENITOR_LLM_PROVIDER` (`openai` | `azure`, default: `azure`)

### OpenAI (public)

* `OPENAI_API_KEY`
* `PROGENITOR_MODEL_DEFAULT`

### Azure OpenAI (deployments)

* `AZURE_OPENAI_ENDPOINT` (your resource endpoint)
* `AZURE_OPENAI_API_KEY`
* `AZURE_OPENAI_API_VERSION` (use the v1 APIs when available)
* `AZURE_OPENAI_DEPLOYMENT_DEFAULT` (deployment name)

Optional per-role deployments (override the default deployment):

* `AZURE_OPENAI_DEPLOYMENT_PLANNER`
* `AZURE_OPENAI_DEPLOYMENT_BUILDER`
* `AZURE_OPENAI_DEPLOYMENT_SUMMARY`

Notes:

* Model selection is done by **deployment name** on Azure.
* Ensure your deployments include **GPT‑5 series** capability if you want GPT‑5 support.

### Runtime

* `PROGENITOR_TIMEZONE` (default: `Australia/Melbourne`)
* `PROGENITOR_IDLE_BACKOFF_SECONDS` (default: `30`)
* `PROGENITOR_MAX_ACTIONS_PER_CYCLE` (default: `10`)
* `PROGENITOR_LOG_LEVEL` (`debug|info|warn|error`, default: `info`)

### Storage

* `PROGENITOR_STATE_DIR` (default: `/state`)
* `PROGENITOR_LOG_DIR` (default: `/logs`)
* `PROGENITOR_WORKTREE_DIR` (default: `/worktrees`)
* `PROGENITOR_STATE_LOCK_TIMEOUT_SECONDS` (default: `10`)

### Copilot

* `PROGENITOR_COPILOT_BIN` (default: `copilot`)
* `PROGENITOR_COPILOT_TIMEOUT_SECONDS` (default: `720` - 12 minutes for complex builds)

### Scheduler

* `PROGENITOR_SCHEDULER_ENABLED` (`true|false`, default: `true`)
* `PROGENITOR_SCHEDULER_POLL_SECONDS` (default: `30`)

### Backend

* `CHAT_SERVER_HOST` (default: `0.0.0.0`)
* `CHAT_SERVER_PORT` (default: `8000`)
* `CHAT_SERVER_DEBUG` (`true|false`, default: `false`)
* `CHAT_SERVER_PROGENITOR_TIMEOUT` (default: `300`)
* `CHAT_SERVER_MODEL_NAME` (default: `progenitor-v1`)

Startup behaviour:

* Log the resolved configuration (with secrets redacted) to `logs/decisions.jsonl`.

---

## Transport Abstraction

Messaging is treated as just another input/output stream.

* Progenitor does **not** embed WhatsApp logic in the core loop
* A transport interface handles:

  * receive(message)
  * send(message, destination)

WhatsApp is simply the first concrete implementation. Other transports (CLI, webhooks, Slack, Matrix) can be added later.

---

## Backend (FastAPI Gateway)

The Backend is a separate FastAPI application that provides an **OpenAI-compatible REST API** for interacting with Progenitor.

### Architecture

* **Separate process**: Backend runs independently of Progenitor
* **IPC-first communication**: Uses socket-based IPC to communicate with the Progenitor runtime (legacy subprocess path remains for compatibility)
* **OpenAI compatibility**: Implements `/v1/responses` and `/v1/models` endpoints
* **Streaming support**: Supports SSE streaming responses

### Endpoints

* `GET /health` - Health check
* `GET /v1/models` - List available models (OpenAI-compatible)
* `POST /v1/responses` - Primary OpenAI-compatible response endpoint
* `POST /chat` - Simple chat endpoint for quick testing

### Communication Protocol

Backend-to-agent communication is implemented through the IPC protocol under `app/progenitor/ipc/`, with request/response payloads serialized over sockets.

### Running

```bash
# Start Backend (will spawn Progenitor on first request)
uvicorn app.backend.main:app --host 0.0.0.0 --port 8000

# Or with auto-reload for development
uvicorn app.backend.main:app --reload
```

### Usage with OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed"  # API key not required for local
)

response = client.responses.create(
    model="progenitor-v1",
    input=[{"role": "user", "content": "Hello!"}]
)
print(response.output_text)
```

---

## State & Persistence Model

### Storage Rules

* All core state is JSON
* Append‑only logs are JSONL (one object per line)
* **File locks are only for the core files below** (not every random artefact)
* No in‑memory‑only critical state
* Atomic writes for JSON (temp → fsync → rename)

### Core Files

* `data/state/build_state.json` (backlog, messages, ideal_state, verification)
* `data/state/current_state.json` (user session: messages, tool calls, attachments)
* `data/config/agents.json` (agents)
* `data/config/mcp.json` (MCP tools)
* `data/state/schedule.json` (system feature: scheduled tasks, persists beyond sessions)
* `logs/conversation.jsonl`
* `logs/decisions.jsonl`
* `logs/copilot_calls.jsonl`
* `logs/validation.jsonl`
* `logs/schedule.jsonl`

State is always recoverable by replaying JSON.

---

## Lessons Borrowed from `llm-loop`

Practical ideas worth stealing:

* **Conversation as the source of truth**: treat conversation history as the primary artefact. Persist the canonical stream in `logs/conversation.jsonl` and rebuild context from it.
* **Tool/provider pattern**: treat tools as a discoverable set (built-in + custom) rather than hardcoding everything.

  * keep default tools in a known module path (inside the container image)
  * allow custom tool files to be mounted and registered at runtime
* **Prompt templates as code**: keep routing/build prompts as versioned templates, not scattered strings.
* **Modular layout**: separate conversation manager, tool system, prompt templates, config, and validation.

---

## Core State (Minimal)

Keep only what Progenitor needs to persist and recover. Any detailed schemas belong to the specialised agents.

Core JSON contains:

* run counters + cycle metadata
* backlog queue + statuses
* **schedule registry** (recurring and one‑off tasks)
* **agent registry** (A2A agents: name → capability → entrypoint)
* **tool registry** (including MCP servers Progenitor can call)
* last decisions + last actions (high level)

---

## LangGraph Topology

### Nodes

* **Intake**: append user message to conversation log; update current context
* **Chat**: main LLM tool-calling interaction for user input
* **PickNext**: select next backlog item
* **WorktreePrep**: create/checkout worktree for build items
* **Act**:

  * **A2A Call**: invoke existing agent(s) with scoped request
  * **Codex Build (Agent)**: create/upgrade an A2A agent
  * **Codex Build (MCP)**: create/upgrade an MCP server/tooling
* **Validate**: run checks/tests when code changed (configurable)
* **Register**: update registry after successful build (agents + MCP tools)
* **CommitCore**: lock + write core JSON files; append JSONL events
* **Respond**: respond to the user with outcomes and next actions
* **Idle**: if backlog empty and nothing scheduled is due soon, prompt *“Anything to do?”* and back off/sleep until input or next due time
* **CycleLimit**: cap actions per cycle to avoid runaway loops

Background scheduling graph nodes:

* **PickDueSchedule**: find the next due schedule entry and materialize its instruction into `current_input`
* **AdvanceSchedule**: update `schedule.json` (set `last_run`, and next `next_run` from `cron_expression` for recurring entries)

### Flow

User outer loop:

* Intake → Chat → Respond → CycleLimit → (PickNext | Idle)

Execution path (when work is required):

* PickNext → WorktreePrep → Act → Validate → (PickNext | CommitCore) → Respond

Background scheduled jobs (separate graph):

* PickDueSchedule → Intake → Chat → Respond → AdvanceSchedule

The loop consumes backlog continuously for build/tool work. Scheduled items are executed by the background job graph (they do not inject scheduled work into the backlog).

---

## Copilot CLI Integration

Copilot CLI is the **builder actuator** for all build operations:

* A2A agents
* internal tools
* **MCP servers** (tool expansion)

Progenitor feeds Copilot tightly scoped prompts and executes it inside an isolated git worktree.

### Invocation Pattern

Copilot is invoked in non-interactive, "just do it" mode.

Example:

```
copilot --model "claude-haiku-4.5" \
        -p "create a readme for an agent" \
        --yolo
```

Rules:

* Model selection is explicit per invocation
* Prompts must reference acceptance criteria and target paths
* Prefer minimal diffs
* Require tests/validation updates when behaviour changes

### Logging

Each invocation appends a JSON object to `logs/copilot_calls.jsonl` containing:

* work_item_id, worktree_path
* resolved command (including model)
* prompt text
* stdout/stderr references
* resulting `git diff` reference

---

## Agent Orchestration (A2A)

Progenitor is the **front door**. It can A2A-call any agent it has created.

### Registry and Discovery

* Maintain `data/config/agents.json` with agent manifests: name, endpoint/entrypoint, version, and **capability tags**.
* On each request, Progenitor tries to **discover** a matching capability first.
* If a capability is missing, Progenitor creates a backlog item to **build or upgrade** an agent.

Rules:

* Progenitor receives user messages, decides, then either answers, delegates, or builds.
* Specialised agents do the narrow work; Progenitor coordinates.
* Agents may not create other agents; Progenitor owns creation/registry.

---

## MCP Tooling (Servers + Tools)

MCP servers are first-class tools that Progenitor can **build, register, and call**.

### Registry

* MCP tools are stored in `data/config/mcp.json`
* Each entry includes: name, transport (stdio/http), command or endpoint, capability tags

### Build / Upgrade

If a requested capability maps to an MCP tool and none exists:

* Planner creates a backlog item
* Copilot CLI scaffolds or extends the MCP server
* Tool is registered and immediately usable

### Usage

* If a matching MCP capability exists, Progenitor calls it directly
* Otherwise, Progenitor builds or upgrades the MCP server first

---

## Logging & Audit

Primary emphasis: **log all conversation history from the user**.

Append-only JSONL (one object per line):

* `logs/conversation.jsonl`: every user message verbatim + assistant response summary + tool calls (including A2A)
* `logs/decisions.jsonl`: Decide/Planner rationale and routing choices
* `logs/copilot_calls.jsonl`: resolved Codex command + flags + artefact refs
* `logs/validation.jsonl`: checks/tests outcomes

Logs are intentionally verbose. Silence is a bug.

---

## Failure Handling

* Validation failure → backlog item updated and retried
* Repeated failure → backlog item blocked (and reported)
* Catastrophic failure → process exits with state intact; supervisor restarts container

No silent retries. No pretending it worked.

---

## Scheduling & Time Awareness

Progenitor is time-aware and maintains a simple internal scheduler.

* Wall-clock time is read by the background scheduler (container time + configured timezone)
* Time-based intents ("every day at 9am", "next Tuesday") create or modify schedule entries
* Due schedules are executed in a dedicated background job graph
* After execution, `schedule.json` is advanced (set `last_run`, and next `next_run` from `cron_expression` for recurring entries)

Scheduled items may:

* call an A2A agent
* call an MCP tool
* trigger a build/upgrade if the required capability is missing

---

---

## Security & Safety

* All commands are allowable when explicitly requested by the user
* Core rule: **record what happened**
* Secrets are injected, never generated or logged (redact before writing)
* Network defaults are configurable (container-friendly), but actions are always logged

---

## Simple File Structure

Keep it boring. Boring survives.

* `app/`

  * `progenitor/`

    * `main.py` (service entrypoint)
    * `graph_chat_loop.py` (chat loop LangGraph topology)
    * `graph_build_loop.py` (build loop LangGraph topology)
    * `llm/` (OpenAI Responses wrapper + prompt templates)
    * `tools/` (Codex wrapper, git/worktree helpers, MCP runner/client)
    * `runtime/` (loop, idle/backoff, time, scheduler)
    * `state/` (state load/save + locking)
  * `backend/` (FastAPI gateway with OpenAI-compatible interface)

    * `main.py` (FastAPI application)
    * `ipc_client.py` (primary Progenitor IPC client)
    * `client.py` (legacy subprocess client)
    * `models.py` (OpenAI-compatible Pydantic models)
    * `config.py` (server configuration)
  * `agents/` (A2A agent packages created/managed by Progenitor)
  * `mcp/` (MCP servers created/managed by Progenitor)
* `.env` / `.env.example` (runtime and Copilot configuration)
* `data/state/` (mounted volume)

  * `build_state.json`
  * `current_state.json`
  * `schedule.json`
* `data/config/` (mounted volume)

  * `agents.json`
  * `mcp.json`
* `logs/` (mounted volume)

  * `conversation.jsonl`
  * `decisions.jsonl`
  * `codex_calls.jsonl`
  * `validation.jsonl`
  * `schedule.jsonl`
* `worktrees/` (mounted volume)

---

## Genesis Prompt (System)

This is the baseline instruction set Progenitor must load on startup (versioned as a template).

**Identity**

* You are Progenitor: a perpetual, conversational personal assistant and orchestration hub.
* You run inside Docker and persist state/logs to disk. You never assume memory exists unless it is in the logs/state.

**Primary rule: log the human**

* Every user message is written verbatim to `logs/conversation.jsonl` before doing anything else.
* Every response includes a short summary event appended to the same log.

**Decide every turn**

* For each user message, choose one: Answer directly | Delegate via A2A | Use MCP tool | Build/Upgrade capability (agent or MCP) | Mixed.
* Prefer existing capability (A2A/MCP) before building.

**Backlog-driven action**

* Convert work into backlog items; execute one item at a time unless the backlog explicitly groups them.
* Use git worktrees for any code change.

**Scheduling**

* If the user expresses a time-based intent ("every day at 9am", "next Tuesday"), create/modify a schedule entry.
* Scheduled entries are executed by the background job graph and advanced after running.

**Build discipline (Copilot CLI)**

* When building/upgrading agents or MCP servers, keep diffs minimal, update tests if behaviour changes, and log the diff ref.
* Register new/updated capabilities in `data/config/agents.json` immediately after validation.

**Persistence and locking**

* Core state files are written with locks and atomic writes.
* Logs are append-only JSONL; never rewrite history.

**Safety posture**

* All commands are allowable when the user requests them.
* Never log secrets; redact before writing.

**Idle behaviour**

* If no backlog is available and nothing is due soon, idle with backoff and prompt: “Anything to do?”

---

## Definition of Done

Progenitor is operational when it can:

* accept an instruction
* generate/refresh a backlog
* discover and call A2A agents or MCP tools
* build/upgrade agents and MCP servers via Copilot CLI
* validate and register them
* persist everything as JSON/JSONL with locks
* resume cleanly after restart

If that works, the rest is just refinement.
