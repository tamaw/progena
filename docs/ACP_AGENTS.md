# ACP Agents

Progenitor can invoke **ACP agents** — local CLIs that speak the **Agent Client
Protocol** (Zed's stdio JSON-RPC standard), the most prominent being the GitHub
Copilot CLI (`copilot --acp`). ACP is a peer protocol to A2A: where A2A agents are
remote HTTP servers, ACP agents are local subprocesses Progenitor spawns and drives.

> Terminology: this is the **Agent Client Protocol** (the `agent-client-protocol`
> Python SDK, imported as `acp`), *not* IBM's "Agent Communication Protocol" (which
> merged into A2A). It is also the inverse of LangChain's `deepagents-acp`, which
> exposes a deep agent *as* an ACP server.

## How it fits together

```
call_agent tool ─┬─ transport == "http" ─→ app/progenitor/a2a/   (remote A2A task, polled)
                 └─ transport == "acp"  ─→ acp_task:<id> document (queued)
workflows ───────── document_create / document_write(acp_task) ─┘
                                                                 │ document event
backend ACPTaskSupervisor (app/backend/acp_supervisor.py) ◀──────┘
   └─ ACPRunner (app/progenitor/acp/) ─→ ACP agent (TCP server or stdio CLI)

registry (Postgres `registry` document — canonical; read-through to the backend)
   └─ kind=acp_agent, transport=acp, acp={command|host+port, profile, model, cwd, ...}
```

### Where the registry lives (database, not the file)

The runtime registry is **database-backed**: `ToolRegistry` reads and writes the
Postgres `registry` document through `StateManager` (`registry/runtime.py`
`bind_state_manager`). The agent-facing `/config/agents.json` virtual file is a
validated view of that same document. It is not a local file under
`data/config/`. Registering through `register_acp_agent` persists to Postgres,
so a restart picks it up without touching the filesystem. An unbound
`ToolRegistry` instance, such as one used in an isolated unit test, starts with
an in-memory registry and does not provide production persistence.

- **Invocation** — every ACP run is an `acp_task:<id>` document. `call_agent`
  (runtime) and workflows (`document_create`/`document_write`) only write
  requests; the backend's `ACPTaskSupervisor` is the single driver. It reacts to
  the document events, resolves `agent_name` from the registry, and runs turns
  through `ACPRunner` (`app/progenitor/acp/`, built on the `acp` SDK) over
  **TCP** (`host`/`port`, e.g. the Claude container) or **stdio** (spawn a
  `command`; the CLI must then be installed in the backend image).
- **Requests** — create with `status: "queued"` to start a session; write
  `{prompt, status: "queued"}` to run the next turn on the same session (resumed
  with ACP `session/resume`; a running turn is cancelled first, which is how a
  stalled agent is nudged); write `{cancel_requested: true}` to cancel. All
  supervisor writes are atomic read-modify-writes of its own fields, so a request
  written mid-turn is never lost.
- **State** — the supervisor sets `status` (`working`, then `completed`,
  `failed`, `canceled`, or `interrupted`), `session_id`, `turn`, `output`,
  `history`, `tool_calls`, and `progress` (`last_message`, `plan`,
  `tool_call_count`, `last_tool`, `last_activity_at`, saved every
  `PROGENITOR_ACP_PROGRESS_INTERVAL_SECONDS`). `stalled` turns true when a working
  turn shows no activity for `PROGENITOR_ACP_STALL_SECONDS`. Schema:
  `data/schemas/acp_task.schema.v1.json`.
- **Limits** — at most `PROGENITOR_ACP_MAX_SESSIONS` turns run at once (extra
  tasks stay `queued`); a task that already started `PROGENITOR_ACP_MAX_TURNS`
  turns fails its next request.
- **Model** — when the registry entry sets `model`, it is selected per session:
  `session/set_config_option` (`configId: "model"`) for agents that expose models
  as config options (Claude ACP), else `session/set_session_model`.
- **Completion** — the idle heartbeat (`nodes/acp.py`) picks up finished tasks
  with `notify_on_completion` (set by non-blocking `call_agent`), appends a
  conversation message, and notifies the user. Blocking `call_agent` polls the
  document until it finishes. Workflows chain on the document events instead.
- **Storage / visibility** — ACP agents live in the same registry as A2A agents, so
  they are invocable by the runtime and read-through to the backend Agents page
  (grouped under "ACP agents"), exactly like A2A agents.

## Registering an ACP agent

No ACP agents are seeded by default — register the ones you want with the
`register_acp_agent` tool, which persists to the database. The simplest form is a
**connection string**:

- `tcp://192.168.0.2:3000?model=claude-haiku-4.5&cwd=/tmp` — connect to a running
  `copilot --acp --port 3000` server (TCP; the primary mode).
- `stdio://copilot` or a bare `copilot` — spawn a local stdio agent.

Explicit fields (`host`/`port`/`command`/`profile`/`model`/`cwd`) override anything
derived from the connection string. A known `profile` (e.g. `"copilot"`) supplies
stdio CLI flags and the `--yolo`/model behaviour; otherwise pass raw `args`.

Remove an agent with the `unregister_agent` tool (by name) — it deletes any agent
(ACP or A2A) from the database registry. The A2A counterpart for *adding* agents is
`register_a2a_agent` (by URL).

## Working directory & safety

Each task runs in its `cwd` (a path on the agent's host), else the registry
entry's `acp.cwd`, else `/tmp`. For build work pass a worktree the `repo-manager`
agent created under `/app/repos` (`PROGENITOR_WORKTREE_DIR`): it is mounted at
the same path in the runtime, backend, and Claude containers, all running as
UID 10001, so the path the agent reports is the one Claude edits.

Permission requests are auto-approved (an allow option is chosen) — **the
working directory is the guardrail, not per-call prompting**. Claude has no
GitHub credentials; pushing and pull requests are the `repo-manager` agent's job
(`git_publish: true`), using the runtime's `GH_TOKEN`.

## Restarts

On backend startup the supervisor marks tasks left `working` as `interrupted`
(their connection died with the process) and starts tasks left `queued`. On
shutdown in-flight turns are cancelled and marked `interrupted`. Claude keeps
sessions on disk (`/home/claude/.claude`), so queueing a new prompt on an
interrupted task resumes the same conversation.

## Limitations

- New ACP agents are managed via the `register_acp_agent` tool / config edits — no
  backend REST API or Agents-page form yet.
