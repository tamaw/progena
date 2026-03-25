# DeepAgent Configuration Specification

> Compares the DeepAgent configuration across all agents in the Progenitor system.
> The goal is to document what each agent shares, what it diverges on, and why,
> so that the configuration can be reconstructed or a new agent can be designed
> consistently.

---

## Agents Covered

| Agent | Location | Uses `create_deep_agent` | Role |
|---|---|---|---|
| **Progenitor** | `app/progenitor/agent/deep_agent.py` | Yes | Conversational hub, decision router, perpetual outer loop |
| **Builder Progenitor** | `app/agents/builder_progenitor/agent.py` | Yes | Code builder using GitHub Copilot ACP, A2A server |
| **Builder Agent** | `app/agents/builder_agent/agent.py` | No (raw LangGraph) | Inner-loop build runner, A2A server |
| **Observe Agent** | `app/agents/observe_agent/agent.py` | No (raw OpenAI + LangGraph) | Read-only diagnostics, A2A server |

---

## Shared Properties (All DeepAgent Agents)

The following properties are common to all agents that use `create_deep_agent`:

| Property | Value / Pattern |
|---|---|
| **Framework** | `deepagents.create_deep_agent` from the `deepagents` library |
| **LLM Provider** | Azure OpenAI Responses API (all agents point to `AZURE_OPENAI_BASE_URL`) |
| **LangChain Model** | `ChatOpenAI` (or subclass) configured via `langchain_openai` |
| **Checkpointer** | LangGraph `MemorySaver` or `AsyncSqliteSaver` for conversation persistence |
| **System Prompt** | Loaded from a prompt file or constructed at startup; passed as `system_prompt` |
| **Tools** | A list of `BaseTool`-compatible callables registered at construction time |
| **Return type** | `CompiledStateGraph` — a LangGraph state graph ready for `invoke`/`stream` |
| **Transport** | A2A JSON-RPC 2.0 over HTTP (FastAPI) on a configurable `PORT` env var |

---

## Per-Agent Configuration

### 1. Progenitor

**Source:** `app/progenitor/agent/deep_agent.py`  
**Config class:** `ProgenitorConfig` (`app/progenitor/config.py`)

#### Factory signature

```python
create_progenitor_agent(
    config: ProgenitorConfig | None = None,
    checkpointer: Any | None = None,
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    store: Any | None = None,
    project_root: str | None = None,
    log_dir: str | Path | None = None,
    skills_dirs: list[str] | None = None,
    memory_paths: list[str] | None = None,
    inner_loop_graph: Any | None = None,
) -> CompiledStateGraph
```

#### DeepAgent call

```python
create_deep_agent(
    name="progenitor",
    model=model,                  # ChatOpenAI via create_langchain_model(config.llm)
    system_prompt=system_prompt,  # Canonical genesis prompt from PromptTemplates
    tools=tools,                  # Progenitor's system tools wrapped by tools_adapter
    backend=backend,              # Core-only (no shell), create_progenitor_backend()
    store=store,                  # InMemoryStore (dev) or injected BaseStore
    skills=skills_dirs,           # Auto-discovered from prompts/skills/ or injected
    memory=memory_paths,          # Auto-discovered AGENTS.md or injected
    subagents=None,               # No subagents (inner loop injected separately)
    middleware=custom_middleware,  # [SecretRedaction, AuditLogging, Metrics]
    checkpointer=checkpointer,    # AsyncSqliteSaver (prod) or MemorySaver (dev)
)
```

#### LLM Config (`LLMConfig`)

| Field | Env var | Default |
|---|---|---|
| `base_url` | `AZURE_OPENAI_BASE_URL` | `""` |
| `api_key` | `AZURE_OPENAI_API_KEY` | `None` |
| `model_default` | `PROGENITOR_MODEL_DEFAULT` | `"gpt-5.2-chat"` |
| `model_planner` | `PROGENITOR_MODEL_PLANNER` | `None` |
| `model_builder` | `PROGENITOR_MODEL_BUILDER` | `None` |
| `model_summary` | `PROGENITOR_MODEL_SUMMARY` | `None` |
| `reasoning_enabled` | `PROGENITOR_REASONING_ENABLED` | `False` |
| `reasoning_budget_tokens` | `PROGENITOR_REASONING_BUDGET_TOKENS` | `None` |

#### Runtime Config (`RuntimeConfig`)

| Field | Default | Notes |
|---|---|---|
| `timezone` | `"Australia/Melbourne"` | Scheduling timezone |
| `idle_backoff_seconds` | `30` | Base idle sleep between outer loop cycles |
| `max_actions_per_cycle` | `10` | Failsafe for inner loop actions per cycle |
| `log_level` | `"info"` | |
| `log_format` | `"text"` | `"text"` or `"json"` |
| `max_inner_iterations` | `5` | Inner build loop iteration cap |
| `min_verification_score` | `0.8` | Build pass threshold |
| `ipc_enabled` | `True` | Socket IPC with backend |
| `ipc_host` | `"127.0.0.1"` | |
| `ipc_port` | `9500` | |

#### Storage Config (`StorageConfig`)

| Field | Env var | Default |
|---|---|---|
| `state_dir` | `PROGENITOR_STATE_DIR` | `data/state` |
| `log_dir` | `PROGENITOR_LOG_DIR` | `logs/` |
| `worktree_dir` | `PROGENITOR_WORKTREE_DIR` | `worktrees/` |

#### Backend

- **Type:** Core-only (no project filesystem, no shell)
- **Purpose:** Progenitor does not directly execute shell commands; it delegates to builder agents via A2A.

#### Middleware Stack

Applied in order after DeepAgent's built-in middleware:

1. **`SecretRedactionMiddleware`** — strips API keys, tokens, and credentials from all message content before logging or persisting.
2. **`AuditLoggingMiddleware`** — writes structured JSONL audit entries to `log_dir` for every message exchange.
3. **`MetricsMiddleware`** — records latency, token counts, and tool invocation stats.

#### Skills

- Auto-discovered from `prompts/skills/` relative to `project_root` (or `cwd`).
- Can be overridden by passing `skills_dirs`.
- Skills are loaded as progressive-disclosure system prompt fragments.

#### Memory

- Auto-discovers `AGENTS.md` from the project root.
- Can be overridden by passing `memory_paths`.
- Memory files are injected into the agent's context at startup.

#### Checkpointer

- **Production:** `AsyncSqliteSaver` backed by `{state_dir}/conversations.db`
- **Dev/test fallback:** `MemorySaver` (in-process only, no persistence between restarts)

#### Subagents

- Currently `None`.
- The inner build loop graph can be injected via `inner_loop_graph` for future use.

---

### 2. Builder Progenitor

**Source:** `app/agents/builder_progenitor/agent.py`  
**Config class:** `BuilderConfig` (`app/agents/builder_progenitor/config.py`)

#### Factory signature (internal)

```python
_create_graph(
    config: BuilderConfig,
    project_root: Path,
    log_dir: Path,
    *,
    name: str,
    system_prompt: str,
) -> CompiledStateGraph
```

#### DeepAgent call

```python
create_deep_agent(
    name=name,                     # Resolved from AGENT_NAME env var
    model=model,                   # LoggingChatOpenAI (ChatOpenAI subclass with JSONL logging)
    system_prompt=system_prompt,   # Loaded from prompts file at runtime
    tools=tools,                   # Builder-specific tools: git, copilot, shell, etc.
    skills=skills,                 # Auto-discovered from agent's own skills/ directory
    backend=backend,               # LocalShellBackend (full shell, project filesystem)
    middleware=[],                 # No custom middleware
    checkpointer=MemorySaver(),    # Always in-memory (stateless per task)
)
```

#### LLM Config (`LLMConfig`)

| Field | Env var | Default |
|---|---|---|
| `base_url` | `AZURE_OPENAI_BASE_URL` | `""` |
| `api_key` | `AZURE_OPENAI_API_KEY` | `None` |
| `model_default` | `AGENT_MODEL_DEFAULT` | `"gpt-5-mini"` |
| `model_planner` | `OVERRIDE_AGENT_PLANNER_MODEL` | `None` |
| `model_builder` | `OVERRIDE_AGENT_BUILDER_MODEL` | `None` |
| `model_summary` | `PROGENITOR_MODEL_SUMMARY` | `None` |

> **Note:** No `reasoning_enabled` or `reasoning_budget_tokens` — these are Progenitor-specific.

#### Storage Config (`StorageConfig`)

| Field | Env var | Default |
|---|---|---|
| `state_dir` | `PROGENITOR_STATE_DIR` | `/data/state` (Docker) |
| `log_dir` | `PROGENITOR_LOG_DIR` | `/logs` (Docker) |
| `worktree_dir` | `PROGENITOR_WORKTREE_DIR` | `/worktrees` (Docker) |

#### Copilot Config (`CopilotConfig`)

| Field | Env var | Default |
|---|---|---|
| `bin_path` | `PROGENITOR_COPILOT_BIN` | `"copilot"` |
| `timeout_seconds` | `PROGENITOR_COPILOT_TIMEOUT_SECONDS` | `1200` |
| `additional_flags` | `PROGENITOR_COPILOT_ADDITIONAL_FLAGS` | `[]` |
| `model` | `DEFAULT_COPILOT_MODEL` | `"gpt-5-mini"` |
| `allowed_dirs` | `PROGENITOR_WORKTREES_ROOT` | `["/worktrees"]` |

#### Backend

- **Type:** `LocalShellBackend`
- **Root:** `project_root` (the agent's worktree)
- **Virtual mode:** `False` — all shell commands run for real
- **Inherit env:** `True` — shell inherits all process environment variables
- **Timeout:** `min(config.copilot.timeout_seconds, 1200)` seconds

#### Middleware Stack

- **None** — builder_progenitor applies no custom middleware.

#### Skills

- Discovered from `app/agents/builder_progenitor/skills/` if it exists.
- Passed as a relative path from `project_root`.

#### Memory

- Not configured — no memory files auto-loaded.

#### Checkpointer

- Always `MemorySaver()` — each build task is stateless and independent.

#### Subagents

- Not configured.

---

### 3. Builder Agent

**Source:** `app/agents/builder_agent/agent.py`  
**Config class:** `BuilderConfig` (`app/agents/builder_agent/config.py`)

> **Does not use `create_deep_agent`.** Uses a raw LangGraph `StateGraph` with a hand-crafted inner-loop (OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN).

#### Architecture

- Exposes an A2A server via FastAPI.
- Accepts tasks via an async queue (`BuilderTaskQueue`).
- Runs each task in a background coroutine through `BackgroundBuildRunner`.
- Uses `CopilotACPManager` to invoke GitHub Copilot CLI as the primary builder tool.
- Uses a `StateManager` for task-level state persistence.
- Uses a `ToolRegistry` for tool lookup.

#### LLM Config (`LLMConfig`)

| Field | Env var | Default |
|---|---|---|
| `base_url` | `AZURE_OPENAI_BASE_URL` | `""` |
| `api_key` | `AZURE_OPENAI_API_KEY` | `None` |
| `model_default` | `AGENT_MODEL_DEFAULT` | `"gpt-5-mini"` |

> Simplified — no per-role model overrides at the LLM config level; overrides are applied via `OVERRIDE_AGENT_PLANNER_MODEL` / `OVERRIDE_AGENT_BUILDER_MODEL` at runtime.

#### Storage Config (`StorageConfig`)

Same structure as builder_progenitor:

| Field | Env var | Default |
|---|---|---|
| `state_dir` | `PROGENITOR_STATE_DIR` | `/data/state` |
| `log_dir` | `PROGENITOR_LOG_DIR` | `/logs` |
| `worktree_dir` | `PROGENITOR_WORKTREE_DIR` | `/worktrees` |

#### Copilot Config (`CopilotConfig`)

Same structure as builder_progenitor.

#### Inner Loop

The 7-phase build loop is implemented directly in LangGraph nodes:

1. **OBSERVE** — refine ideal state from task description
2. **THINK** — generate approaches, trade-offs, risks
3. **PLAN** — select experiment and measurable success criteria
4. **BUILD** — produce candidate output (via Copilot ACP)
5. **EXECUTE** — run tests, linters, shell commands
6. **VERIFY** — score output against ideal state (threshold: 0.8)
7. **LEARN** — update strategy and ideal state for next iteration

---

### 4. Observe Agent

**Source:** `app/agents/observe_agent/agent.py`

> **Does not use `create_deep_agent`.** Uses a minimal LangGraph `StateGraph` with direct OpenAI API calls via `httpx`.

#### Architecture

- Lightweight read-only diagnostic agent.
- No config dataclass — reads env vars directly at runtime.
- Calls Azure OpenAI `/responses` endpoint directly via `httpx`.
- Reads mounted state files and log files (JSONL) from shared volumes.
- Exposes A2A server via FastAPI.

#### Capabilities

| Capability | Description |
|---|---|
| `progenitor_health_report` | Reads current state and reports health |
| `diagnose_last_error` | Tails logs and identifies recent errors |
| `tail_state_and_logs` | Returns raw tail of state and log files |

#### LLM Configuration (inline, no dataclass)

| Env var | Purpose | Default |
|---|---|---|
| `AZURE_OPENAI_BASE_URL` | Base URL (auto-appends `/responses`) | `""` |
| `AZURE_OPENAI_API_KEY` | API key | `""` |
| `AGENT_MODEL_DEFAULT` | Model name | `"gpt-5-mini"` |

#### Backend / Storage

- No shell execution.
- Reads files from paths resolved by `PROGENITOR_STATE_DIR` and `PROGENITOR_LOG_DIR` env vars.
- Max 512 KB per file read (safe partial reads).

---

## Similarity / Difference Matrix

| Aspect | Progenitor | Builder Progenitor | Builder Agent | Observe Agent |
|---|---|---|---|---|
| **Uses `create_deep_agent`** | ✅ | ✅ | ❌ | ❌ |
| **LLM Provider** | Azure OpenAI | Azure OpenAI | Azure OpenAI | Azure OpenAI |
| **LangChain model** | `ChatOpenAI` | `LoggingChatOpenAI` | Direct client | `httpx` (raw) |
| **Shell backend** | ❌ Core-only | ✅ `LocalShellBackend` | ✅ via Copilot ACP | ❌ None |
| **Middleware** | ✅ Secret+Audit+Metrics | ❌ None | ❌ N/A | ❌ N/A |
| **Skills** | ✅ `prompts/skills/` | ✅ agent `skills/` dir | ❌ N/A | ❌ N/A |
| **Memory files** | ✅ `AGENTS.md` | ❌ None | ❌ N/A | ❌ N/A |
| **Checkpointer** | SQLite (prod) / Memory | Memory only | N/A | N/A |
| **Subagents** | None (planned) | None | N/A | N/A |
| **Per-role models** | ✅ planner/builder/summary | ✅ planner/builder | ❌ single model | ❌ single model |
| **Reasoning features** | ✅ `reasoning_enabled` | ❌ | ❌ | ❌ |
| **Copilot ACP** | ❌ | ✅ | ✅ | ❌ |
| **Config dataclass** | `ProgenitorConfig` | `BuilderConfig` | `BuilderConfig` | Inline env vars |
| **Copilot config** | ❌ | ✅ `CopilotConfig` | ✅ `CopilotConfig` | ❌ |
| **A2A server** | ❌ (IPC only) | ✅ | ✅ | ✅ |
| **State persistence** | SQLite conversations | In-memory per task | JSONL files | Read-only |
| **Loop type** | Perpetual outer + delegated inner | Single-task inner loop | Single-task 7-phase loop | Single-query |
| **System prompt source** | `PromptTemplates.get_genesis_prompt()` | Loaded from prompts file | Hardcoded or file | Inline |

---

## Common Environment Variables

All agents read the following environment variables:

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_BASE_URL` | Azure OpenAI endpoint base URL |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AGENT_NAME` | Container identity / log routing |
| `PROGENITOR_LOG_DIR` | Log output directory |
| `PROGENITOR_STATE_DIR` | State persistence directory |

---

## Agent-Specific Environment Variables

| Variable | Progenitor | Builder Progenitor | Builder Agent | Observe Agent |
|---|---|---|---|---|
| `PROGENITOR_MODEL_DEFAULT` | ✅ | ❌ | ❌ | ❌ |
| `AGENT_MODEL_DEFAULT` | ❌ | ✅ | ✅ | ✅ |
| `OVERRIDE_AGENT_PLANNER_MODEL` | ❌ | ✅ | ✅ | ❌ |
| `OVERRIDE_AGENT_BUILDER_MODEL` | ❌ | ✅ | ✅ | ❌ |
| `DEFAULT_COPILOT_MODEL` | ❌ | ✅ | ✅ | ❌ |
| `PROGENITOR_REASONING_ENABLED` | ✅ | ❌ | ❌ | ❌ |
| `PROGENITOR_REASONING_BUDGET_TOKENS` | ✅ | ❌ | ❌ | ❌ |
| `PROGENITOR_COPILOT_BIN` | ❌ | ✅ | ✅ | ❌ |
| `PROGENITOR_COPILOT_TIMEOUT_SECONDS` | ❌ | ✅ | ✅ | ❌ |
| `PROGENITOR_WORKTREES_ROOT` | ❌ | ✅ | ✅ | ❌ |
| `GH_TOKEN` | ❌ | ✅ | ✅ | ❌ |
| `COPILOT_GITHUB_TOKEN` | ❌ | ✅ | ✅ | ❌ |
| `PROGENITOR_REPO_URL` | ❌ | ✅ | ✅ | ❌ |
| `TELEGRAM_BOT_TOKEN` | ✅ | ❌ | ❌ | ❌ |
| `TELEGRAM_ALLOWED_USERS` | ✅ | ❌ | ❌ | ❌ |

---

## Reconstruction Checklist

To reconstruct or add a new agent consistently with the existing agents:

1. **Choose architecture**: `create_deep_agent` (conversational/builder) or raw LangGraph (loop-heavy/diagnostic).
2. **Define config dataclass**: include `LLMConfig`, `StorageConfig`. Add `CopilotConfig` if the agent uses Copilot ACP.
3. **Select LLM model**: default to `AGENT_MODEL_DEFAULT` env var unless the agent is Progenitor (uses `PROGENITOR_MODEL_DEFAULT`).
4. **Choose backend**: `LocalShellBackend` for agents that execute code; core-only or no backend for read-only agents.
5. **Configure middleware**: add `SecretRedactionMiddleware` + `AuditLoggingMiddleware` + `MetricsMiddleware` if the agent handles user-facing messages.
6. **Load skills**: auto-discover from the agent's own `skills/` directory or from `prompts/skills/` for the main agent.
7. **Load memory**: add `AGENTS.md` for the main agent; optional for sub-agents.
8. **Set checkpointer**: use `AsyncSqliteSaver` for long-running conversational agents; `MemorySaver` for stateless task agents.
9. **Expose A2A server**: all sub-agents expose a FastAPI-based A2A JSON-RPC server on a configured port with a `/health` endpoint.
10. **Set `AGENT_NAME`**: all agents require this env var for log routing and container naming.
