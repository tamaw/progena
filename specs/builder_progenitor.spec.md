# Builder Progenitor — Agent Specification

> Precise contract for `app/agents/builder_progenitor/`.  
> Answers design questions that are implicit in the code but not previously
> documented: non-goals, operational contract, persistence invariants, security
> posture, cancellation semantics, error taxonomy, and canonical terminology.

---

## 1. Purpose (one sentence)

`builder_progenitor` is a single-tenant A2A agent that accepts a natural-language
build request, plans a Git worktree change using an LLM planner, executes it with
GitHub Copilot ACP, validates it, and opens a pull request — then reports the
outcome via a JSONL task log.

---

## 2. Non-Goals

The following capabilities are explicitly **outside scope**.  
Any spec, code, or PR that tries to add these must first revise this section.

| Non-goal | Rationale |
|---|---|
| **No multi-service split** | The planner, executor, and hook runner all run inside one process. No separate sidecar services or microservice decomposition. |
| **No bespoke chat protocol** | All external traffic uses A2A JSON-RPC 2.0 over HTTP exactly as defined by the `a2a-sdk`. No custom WebSocket, SSE, or binary frames. |
| **No hidden cloud dependencies** | Every external call goes through `AZURE_OPENAI_BASE_URL` and `GH_TOKEN` / `COPILOT_GITHUB_TOKEN`, both of which are explicit environment variables. No implicit cloud SDK credential chains (no MSI, no IMDSv2, no ambient AWS/GCP roles). |
| **No implicit `.env` in production** | `load_dotenv()` is present for developer convenience only. In a container the `.env` file is not mounted; all config arrives via explicit env vars. |
| **No unbounded tool execution** | Each Copilot ACP and shell tool invocation has an explicit `timeout_seconds` limit (default 1200 s for Copilot, 120 s for `gh`/`git` CLI calls). The LangGraph graph also has a wall-clock `timeout` (300 s per model call, 5 retries). There is no mode that allows unlimited execution time. |
| **No streaming responses to the caller** | The A2A `AgentCapabilities` sets `streaming=False`. The only response to `execute()` is a single `submitted` status update. Result retrieval is poll-based via the JSONL task log, not a streaming HTTP connection. |
| **No multi-tenant isolation** | The agent runs one instance per container, serving one caller. It performs no per-caller isolation of filesystem, credentials, or model context. |
| **No built-in authentication** | See §5. The service is designed to sit behind a gateway that handles identity. |

---

## 3. Operational Contract

### 3.1 Readiness vs Liveness

These two concepts are distinct.  The agent exposes a single `/health` endpoint;
its meaning changes depending on whether `service.initialize()` has completed.

| State | Meaning | `/health` response |
|---|---|---|
| **Starting** | Process is alive; `_lifespan` has not yet returned from `initialize()`. | Not yet reachable (Uvicorn hasn't bound the port; or bound but queue not started). |
| **Ready (Liveness OK)** | All readiness checks below have passed; `BuilderTaskQueue` is running; requests are accepted. | `200 OK` with health body (see §3.2). |
| **Shutting down** | `_lifespan` has received the shutdown signal; queue is draining. | `200 OK` until Uvicorn stops (SIGTERM → 30 s graceful period). |

**Readiness checks** (performed inside `BuilderService.initialize()`, in order):

1. `AGENT_NAME` is set and non-empty — raises `RuntimeError` immediately if missing.
2. `config.storage.state_dir`, `config.storage.log_dir`, `config.storage.worktree_dir` can be created with `mkdir(parents=True, exist_ok=True)`.
3. `get_config()` resolves without raising (all required env vars present with valid types).
4. `_create_graph()` compiles the LangGraph without error — the model client is constructed but **not** called during startup.
5. `BuilderClient` (planner) is constructed without error.
6. `BuilderTaskQueue.start()` successfully posts the background worker coroutine to the event loop.

If any readiness check fails the process exits non-zero and the container health check
(`GET /health`) will not return `200` because Uvicorn will not have started (checks 1–5)
or will have crashed (check 6).

**Liveness** after startup means:

- The event loop is alive (Uvicorn is serving requests).
- `BuilderTaskQueue._worker_task` is not done/cancelled.
- The process has not set `service._shutting_down = True`.

The `/health` endpoint currently does **not** check task-queue health or model
reachability. It is a shallow liveness probe only. A deep readiness probe would
need a separate `/ready` endpoint — that is a future improvement, not current contract.

### 3.2 Health Payload Shape

`GET /health` returns `200 OK` with `Content-Type: application/json` and the
following fixed schema.  No field is optional; all are always present.

```json
{
  "status": "healthy",
  "name": "<AGENT_NAME env var value>",
  "version": "0.2.0",
  "uptime_seconds": 42.7
}
```

| Field | Type | Description |
|---|---|---|
| `status` | `string` | Always `"healthy"` while the process is alive. Not a deep check. |
| `name` | `string` | Value of the `AGENT_NAME` environment variable, e.g. `"builder_progenitor"`. |
| `version` | `string` | Semver string from `AGENT_VERSION` constant in `agent.py`. |
| `uptime_seconds` | `float` | Seconds since process start (`time.time()` at `BuilderService.__init__`). |

A `404` or connection refused means the service has not started.  
A `500` from `/health` indicates an unhandled error in the health handler itself
and should be treated as a liveness failure.

---

## 4. Persistence Invariants

### 4.1 State Root Rule

> **All durable state must live under the single configured state root.**  
> No directory outside the three canonical paths (`state_dir`, `log_dir`, `worktree_dir`)
> is written to during normal operation.

| Canonical path | Env var | Default (container) | Contents |
|---|---|---|---|
| `state_dir` | `PROGENITOR_STATE_DIR` | `/app/data/state` | Reserved for future use (currently unused by builder_progenitor) |
| `log_dir` | `PROGENITOR_LOG_DIR` | `/app/logs` | Agent log file, LLM JSONL audit trail, task completion JSONL |
| `worktree_dir` | `PROGENITOR_WORKTREE_DIR` | `/app/worktrees/worktrees` | Git worktrees created per build task |

**Portability rule:** swap all three roots simultaneously to move all durable state
to a new host or volume. No other path needs to change.

### 4.2 Task Log Atomicity and Corruption Tolerance

The task completion log (`<log_dir>/<agent_name>/<agent_name>_tasks.jsonl`) uses
a multi-layer write strategy to prevent partial-write corruption:

1. **`fcntl.LOCK_EX` advisory lock** (`<log>.lock` file) — serialises writers
   across processes on the same host (POSIX only).
2. **`threading.Lock`** (`_append_lock`) — serialises writers within the same
   process (always active).
3. **`O_APPEND` mode** (`open(path, "a")`) — POSIX guarantees that writes ≤
   `PIPE_BUF` (4 KiB) are atomic at the kernel level; each JSON line fits easily.
4. **Non-POSIX fallback** (Windows CI): only the in-process `threading.Lock` is
   used; advisory locking is not available.

**Invariants:**

- A record is written **exactly once** per task, in the `finally` block of `_run_task`, even if the task raised.
- A write failure (e.g. volume full) is logged as a warning but does **not** raise; the task's Python outcome is unaffected.
- The file is never truncated or rewritten; only appended to. Historical records accumulate indefinitely (no rotation).
- A corrupt or un-parseable line is silently skipped during reads (`_find_record`).

**Crash mid-write:** Because each JSON line is written in a single `write()` call
under the exclusive lock, a crash during that call may produce a partial line at
the end of the file.  `_find_record` skips lines that fail `json.loads`, so a
partial line is harmless to future reads. No fsync is issued; data in the OS page
cache may be lost on ungraceful host shutdown (e.g. hard power-off), but this is
acceptable — the task itself has already completed.

### 4.3 Worktree Lifecycle

- Each build task creates one Git worktree under `worktree_dir`.
- The worktree path is derived from `work_item_id` (UUIDv4-derived) and is unique
  per task; naming collisions are not possible in normal operation.
- Cleanup of the worktree after a successful or failed task is handled by the
  build tooling (Copilot ACP and `git worktree remove`). The spec does not
  guarantee cleanup on cancellation — cancelled tasks may leave orphaned worktrees.

---

## 5. Security Boundaries

### 5.1 Authentication / Authorisation Stance

> **`builder_progenitor` performs no caller authentication.**  
> It is designed to run on a private Docker network, reachable only from
> `progenitor` (the orchestrator) and the GitHub Actions deploy pipeline.

- There are no API keys, bearer tokens, or mTLS requirements on inbound A2A requests.
- The `progenitor_network` Docker network is the sole access control boundary.
- Any service on that network can submit tasks. This is intentional for the
  single-tenant private deployment model. A multi-tenant or internet-facing
  deployment would require a gateway that authenticates callers before forwarding
  to the agent.

### 5.2 Abuse Controls

| Control | Where | Value |
|---|---|---|
| **Request text length** | `_extract_request_text` | No enforced limit today; truncated to 120 characters in status messages. Future: add a hard reject at e.g. 32 KiB. |
| **Empty request rejection** | `BuilderExecutor.execute()` | `TaskState.failed` returned immediately without enqueuing. |
| **Task queue not initialised rejection** | `BuilderExecutor.execute()` | `TaskState.failed` returned immediately. |
| **Copilot ACP tool timeout** | `config.copilot.timeout_seconds` | Default 1200 s; hard cap min(config, 1200) in `_create_graph`. |
| **`gh`/`git` CLI tool timeout** | `GhCliInput.timeout_seconds` / `GitCliInput.timeout_seconds` | Default 120 s per call. |
| **LangChain model call timeout** | `ChatOpenAI(timeout=300.0)` | 300 s per single model call. |
| **LangChain model retries** | `ChatOpenAI(max_retries=5)` | Up to 5 retries on transient failures. |
| **Concurrent task limit** | `BuilderTaskQueue(max_concurrent=1)` | Only one task runs at a time. Additional submissions queue behind it. |
| **Queue depth** | `asyncio.Queue()` | Unbounded in-process queue. Future: add a max-depth guard (e.g. 20) to prevent memory growth under flood. |
| **Path traversal prevention** | `LocalShellBackend(root_dir=project_root)` | All shell commands are rooted at `project_root`; DeepAgent enforces this boundary. |
| **SSRF / network egress** | Not enforced at the agent level | Outbound calls: `AZURE_OPENAI_BASE_URL` (LLM), `github.com` (gh CLI, git), `PROGENITOR_REPO_URL` (git clone). No egress firewall is currently applied in code; the Docker network and host firewall are the controls. |
| **Rate limiting** | Not enforced at the agent level | Relies on Azure OpenAI rate limits and GitHub API rate limits. The gateway (Progenitor) is responsible for throttling callers. |

---

## 6. Streaming and Cancellation Semantics

### 6.1 Streaming

`builder_progenitor` declares `streaming=False` in its `AgentCapabilities`.

- The caller receives exactly one `TaskStatusUpdateEvent` from `execute()`: a
  `submitted` status with `final=True`. The HTTP connection closes immediately.
- The actual build runs asynchronously in `BuilderTaskQueue._run_task()`.
- There is no mechanism to stream incremental build output back to the caller.
  Result retrieval is poll-based: the caller polls the JSONL task log.

**If background execution is used (it always is):** The only in-flight result
contract is the JSONL record appended on completion. Intermediate state is not
observable via the A2A protocol.

### 6.2 Cancellation

Cancellation is **cooperative**: the `asyncio.CancelledError` is raised at the
next `await` point in the build coroutine.

| Scenario | Cancellation mechanism | Terminal task state |
|---|---|---|
| **Task is still in the queue (not yet started)** | `_cancelled_task_ids.add(task_id)` — the task is skipped when dequeued. | `canceled` (A2A status); no JSONL record written (task never ran). |
| **Task is running (in `process()` or a tool call)** | `asyncio.Task.cancel()` → `CancelledError` raised at next await. | `status="cancelled"`, `phase_completed="cancelled"` in JSONL; `build_cancelled` hook fires. |
| **Task completed before cancel arrives** | `task.done()` is `True`; `cancel_task()` returns `False`. | A2A cancel response says `"No active task to cancel."` but the task record is already written with its actual status. |

**What happens mid-tool on cancellation:**

1. `CancelledError` propagates up through the `await self._service.run_task(...)` call.
2. The `asyncio.Task` wrapping `BuilderService.process()` receives the cancel.
3. The LangGraph graph's `ainvoke` is interrupted at the next `await` inside the
   graph (typically the current model call or tool call).
4. Any LLM generation in flight at the Azure endpoint is abandoned — the
   connection is dropped. Azure billing for the in-flight tokens still applies.
5. Any Copilot ACP subprocess in flight receives `SIGKILL` (via `CopilotCLI`
   cleanup) — unfinished edits to the worktree may remain on disk.
6. The worktree is **not** automatically cleaned up on cancellation. The partially
   modified worktree directory persists under `worktree_dir`.
7. `build_cancelled` hook fires with the partial `planned_build` context (may be
   `None` if cancelled during planning).

**Cancellation is not guaranteed to be instant.** If the current `await` is a
blocking I/O call inside the Copilot ACP subprocess, the cancel may be delayed
until the subprocess produces output or its timeout fires.

### 6.3 Partial Output on Cancellation

- No partial output is returned to the caller via A2A.
- The JSONL record for a cancelled task has `status="cancelled"` and
  `phase_completed="cancelled"`. No `commit_hash` or `pr_url` is recorded.
- Any Git commits already pushed to the remote branch before cancellation
  **remain on the remote**. The cancelled JSONL record contains no `commit_hash`
  even if the commit happened, because the record is written in the `except
  CancelledError` branch before the commit-hash extraction runs.

---

## 7. Error Taxonomy

Errors are categorised by origin and the action they trigger.

### Category A — Configuration Error (fail at startup)

> The process exits non-zero before accepting any requests.

| Error | Trigger | Behaviour |
|---|---|---|
| `AGENT_NAME` not set | `_agent_log_name()` called during `initialize()` | `RuntimeError` → process exits |
| Required directory cannot be created | `p.mkdir(parents=True, exist_ok=True)` raises `PermissionError` | Uncaught exception → process exits |
| Model construction error | `_create_graph()` raises | Uncaught exception → process exits |

### Category B — Request Validation Error (reject fast, no queue entry)

> `execute()` immediately returns `TaskState.failed` without enqueuing.

| Error | Trigger | `final` | Notes |
|---|---|---|---|
| Empty request text | `not request_text` after extraction | `True` | Message: `"Empty request."` |
| Task queue not initialised | `_task_queue is None` | `True` | Message: `"Builder task queue is not initialised."` |
| Builder shutting down | `service._shutting_down` | `True` (via RuntimeError → failure in queue_worker) | Task enqueued but fails immediately in `plan_task`. |

### Category C — Planning Error (task moves to `failed`)

> The LLM planner could not produce a valid `PlannedBuild`.

| Error | Trigger | JSONL `phase_completed` | Notes |
|---|---|---|---|
| LLM API error (timeout, 5xx after retries) | Raised from `BuilderClient.plan_build()` | `"failed"` | `status="failure"` |
| Malformed planner JSON response | Pydantic validation in `BuilderClient` | `"failed"` | `status="failure"` |

### Category D — Build Execution Error (task moves to `failed`)

> The planner succeeded but the build graph raised.

| Error | Trigger | JSONL `phase_completed` | Notes |
|---|---|---|---|
| Copilot ACP subprocess timeout | `asyncio.TimeoutError` inside tool | `"failed"` | `status="failure"` |
| Git operation failure | Tool raises | `"failed"` | `status="failure"` |
| PR creation returned `success=false` | `_assert_result_has_pr_when_committed` | `"failed"` | `RuntimeError` raised; `status="failure"` |
| Committed but no PR URL | `_assert_result_has_pr_when_committed` | `"failed"` | `RuntimeError` raised; `status="failure"` |
| Arbitrary `Exception` | Any uncaught exception in `_run_task` | `"failed"` | `build_failed` hook fires |

### Category E — Cancellation

| State | JSONL `phase_completed` | JSONL `status` |
|---|---|---|
| Cancelled before dequeue | No record written | N/A |
| Cancelled while running | `"cancelled"` | `"cancelled"` |

### Category F — Infrastructure Error (non-fatal, logged as warning)

> The task outcome is unaffected; the error is logged and swallowed.

| Error | Example | Action |
|---|---|---|
| JSONL write failure | Volume full, permission error | `logger.warning`; task record lost |
| Hook invocation failure | Any exception in `_invoke_hook` | Logged; main task result unaffected |

### Retriability

| Category | Retriable? | Notes |
|---|---|---|
| A | No | Fix config and redeploy. |
| B — empty request | No | Caller must fix the request. |
| B — queue not init | Yes | Transient startup race; retry after a few seconds. |
| C — LLM API | Yes | Azure 5xx / timeout; submit again. |
| C — malformed response | Sometimes | May need a different prompt or model. |
| D — Copilot timeout | Yes | May need a smaller task scope. |
| D — git / PR failure | Sometimes | May be a transient GitHub API issue. |
| E | Yes | Submit a new task. |
| F | N/A | Infrastructure issue; no retry needed from caller. |

---

## 8. Canonical Terminology

All code, comments, and documentation for `builder_progenitor` must use this
vocabulary.  Ambiguous synonyms are listed under "Do not use".

### 8.1 Task Lifecycle States

```
submitted → running → completed
                    ↘ failed
                    ↘ cancelled
```

| Canonical term | Meaning | A2A `TaskState` | JSONL `status` / `phase_completed` |
|---|---|---|---|
| **submitted** | Request accepted by `execute()`; enqueued for background processing. | `TaskState.submitted` | — |
| **running** | Task dequeued and `_run_task` is executing (planning or building). | Not emitted via A2A today | — |
| **completed** | Build finished successfully; PR URL exists. | Not emitted via A2A today | `status="success"`, `phase_completed="pr"` |
| **failed** | Build terminated due to an error (any Category C or D error). | Not emitted via A2A today | `status="failure"`, `phase_completed="failed"` |
| **cancelled** | Task was explicitly cancelled before or during execution. | `TaskState.canceled` | `status="cancelled"`, `phase_completed="cancelled"` |

### 8.2 Processing Vocabulary

| Canonical term | Meaning | Do not use |
|---|---|---|
| **task** | A single unit of build work identified by `task_id`. | job, request, work item (in this context) |
| **enqueue** | Add a task to the `BuilderTaskQueue` for deferred execution. | submit to queue, push, dispatch |
| **accepted** | Synonym for `submitted` — the HTTP layer has responded and the task is in the queue. | queued (ambiguous with "still waiting"), deferred |
| **background execution** | The task runs in an `asyncio.Task` after the HTTP response has been sent. | deferred execution path, async processing, fire-and-forget |
| **planning** | The LLM planner step (`BuilderClient.plan_build`) that produces a `PlannedBuild`. | reasoning, thinking, pre-processing |
| **building** | The execution step where Copilot ACP implements the change in a worktree. | running, executing (prefer "building" for the Copilot ACP phase) |
| **task log** | The JSONL file `<log_dir>/<agent_name>/<agent_name>_tasks.jsonl`. | result log, output log, completion log |
| **task record** | A single JSON line in the task log (`TaskRecord` dataclass). | result record, completion record |
| **hook** | A LangGraph invocation triggered on lifecycle events (`build_started`, `build_completed`, `build_failed`, `build_cancelled`). | callback, event handler, notification |

### 8.3 Infrastructure Vocabulary

| Canonical term | Meaning |
|---|---|
| **state root** | The set of three directories: `state_dir`, `log_dir`, `worktree_dir`. |
| **worktree** | A Git worktree created under `worktree_dir` for one task. |
| **queue worker** | The `asyncio.Task` running `BuilderTaskQueue._dequeue_loop()`. |
| **planner** | `BuilderClient` — the LLM client used for planning only. |
| **builder graph** | The compiled LangGraph (`BuilderService._graph`) used for build execution. |
| **hook graph** | The compiled LangGraph (`BuilderService._hook_graph`) used for lifecycle hooks. |
