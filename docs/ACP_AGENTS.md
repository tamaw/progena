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
                 └─ transport == "acp"  ─→ app/progenitor/acp/   (local/remote run, background-tracked)

registry (Postgres `registry` document — canonical; read-through to the backend)
   └─ kind=acp_agent, transport=acp, acp={command|host+port, profile, model, cwd, ...}
```

### Where the registry lives (database, not the file)

The runtime registry is **database-backed**: `ToolRegistry` reads and writes the
Postgres `registry` document through `StateManager` (`registry/runtime.py`
`bind_state_manager`). The agent-facing `/state/agents.json` virtual file is a
validated view of that same document. It is not a local file under
`data/config/`. Registering through `register_acp_agent` persists to Postgres,
so a restart picks it up without touching the filesystem. An unbound
`ToolRegistry` instance, such as one used in an isolated unit test, starts with
an in-memory registry and does not provide production persistence.

- **Invocation** — `call_agent` resolves the agent from the registry and dispatches
  by `transport`. ACP runs are dispatched through `app/progenitor/acp/` (a generic
  client built on the `acp` SDK: `ACPRunner` + `ACPSessionClient`). The runner
  supports both **stdio** (spawn a local `command`) and **TCP** (connect to an
  already-running server via `host`/`port`, e.g. `copilot --acp --port 3000`); in
  TCP mode the `model` is selected per session via `set_session_model`.
- **Tracking** — each run is an `ACPTaskRecord` persisted to the `acp_tasks`
  document. Non-blocking runs execute in a background task that updates the record
  on completion; the idle heartbeat (`nodes/acp.py`) picks up terminal-but-unnotified
  runs, appends a conversation message, and notifies the user. Set `blocking=true`
  to await a short run inline.
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

## Sandboxing & safety

Each ACP run executes in a **fresh per-run sandbox directory** under
`PROGENITOR_ACP_WORKSPACE` (default `data/acp_workspaces/<task_id>/`), passed to the
agent as both its working directory and its allowed dir (Copilot's `--add-dir`).

Copilot runs with `--yolo`, so tool calls are auto-approved — **the filesystem
scope is the guardrail, not per-call prompting**. This mirrors the Builder Agent's
practice of confining Copilot to a worktree. Do not widen the allowed dirs to the
project root unless you intend the agent to modify the live repo.

## Limitations (current iteration)

- A run in flight when the runtime stops is orphaned; its record stays `working`.
- TCP runs target a (possibly remote) server, so the per-run local sandbox does not
  apply — the session `cwd` is a path on the server's filesystem (`acp.cwd`).
- New ACP agents are managed via the `register_acp_agent` tool / config edits — no
  backend REST API or Agents-page form yet.
