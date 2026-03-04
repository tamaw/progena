# Builder Copilot

## 1. Title and Overview
Builder Copilot is a minimal A2A build agent that executes repository changes through a strict, repeatable flow: **plan -> build -> test -> lint -> pr**.
It is implemented in `app/agents/builder_copilot/` and exposed as an A2A service (default port `8108`) with DeepAgent orchestration, worktree isolation, Copilot ACP execution, and PR automation.

## 2. Purpose
Builder Copilot exists to make small, reviewable repository changes safely and consistently.
It is optimized for:
- deterministic execution order
- minimal, targeted diffs
- mandatory validation gates before PR creation
- concise status and final output

## 3. Core Capabilities
- Creates isolated git worktrees per task
- Runs a preflight planning pass before edits
- Uses GitHub Copilot ACP for implementation (`run_copilot_build`)
- Uses scoped Progenitor builds (`build_progenitor`) for `app/progenitor` changes
- Runs focused test and lint commands in the active worktree
- Commits changes and opens GitHub pull requests
- Searches Python sources quickly before edits (`python_search`)
- Emits concise A2A task updates and final artifacts

## 4. Workflow (plan -> build -> test -> lint -> pr)
Builder Copilot follows this order unless the caller explicitly overrides it:

1. **plan**
   - normalize request
   - create/select worktree
   - inspect code before edits
2. **build**
   - implement requested change in worktree
3. **test**
   - run smallest relevant tests first
4. **lint**
   - run narrowest relevant lint/format validation
5. **pr**
   - commit validated changes
   - push branch
   - create PR with summary and test notes

This ordering is enforced by system prompt guidance in `app/agents/builder_copilot/deep_agent.py` and reinforced by skill instructions.

## 5. Available Skills

### plan
**When to use**
- first step of every new task
- when scope, worktree, or validation steps are not explicit

**Tools it uses**
- `summarize_request`
- `create_worktree`
- `python_search`
- `run_worktree_command`

**Typical flow**
1. Normalize request into a stable work item id.
2. Create or reuse worktree.
3. Inspect relevant files and commands.
4. Declare exact build/test/lint/pr path.

---

### build
**When to use**
- after planning is complete
- when implementing code changes, tests, or docs

**Tools it uses**
- `run_copilot_build`
- `build_progenitor` (preferred for Progenitor core changes)
- `run_worktree_command` (for targeted manual follow-up)

**Typical flow**
1. Choose `run_copilot_build` for general repo implementation.
2. Choose `build_progenitor` for `app/progenitor` scope.
3. Review command output and diff.
4. Iterate until change intent is satisfied with minimal edits.

**`run_copilot_build` usage**
- Inputs: `work_item_id`, `worktree_path`, `prompt`, optional `target_path`
- Behavior: executes Copilot ACP build in worktree and returns success, summary, stdout/stderr, diff

**`build_progenitor` usage**
- Inputs: `work_item_id`, `worktree_path`, `prompt`
- Behavior: wrapper over `run_copilot_build` with `target_path="app/progenitor"`

---

### test
**When to use**
- immediately after build
- before lint/pr unless explicitly waived by caller

**Tools it uses**
- `run_worktree_command`
- `execute` (skill fallback)

**Typical flow**
1. Run smallest relevant test command.
2. Capture failing output.
3. Return to build if needed.
4. Re-run until pass.

---

### lint
**When to use**
- after tests pass
- before PR creation

**Tools it uses**
- `run_worktree_command`
- `execute` (skill fallback)

**Typical flow**
1. Run narrowest repo-relevant lint/format command.
2. Fix violations in build step.
3. Re-run lint until clean.

---

### pr
**When to use**
- only after build + test + lint completion (or explicit waiver)

**Tools it uses**
- `commit_changes`
- `create_pull_request`
- `run_worktree_command`

**Typical flow**
1. Verify there are meaningful staged/unstaged changes.
2. Commit with a clear message.
3. Create PR with concise title/body and test notes.
4. Return PR URL in final response.

---

### python-search
**When to use**
- before editing unfamiliar Python code
- when locating symbols, imports, tests, and references

**Tools it uses**
- `python_search`
- `grep`
- `execute`

**Typical flow**
1. Search targeted symbols with `python_search`.
2. Narrow to exact files/lines.
3. Read context before edits.

## 6. Configuration

### Execution environment
Builder Copilot relies on:
- Python 3.12 runtime
- Azure OpenAI Responses endpoint (`AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_API_KEY`)
- GitHub Copilot CLI
- Git + GitHub CLI (`gh`)
- ripgrep (`rg`) for fast source search

Primary config surface (`app/agents/builder_copilot/config.py`):
- LLM: `PROGENITOR_MODEL_DEFAULT`, `PROGENITOR_MODEL_PLANNER`, `PROGENITOR_MODEL_BUILDER`
- Copilot: `PROGENITOR_COPILOT_BIN`, `PROGENITOR_COPILOT_MODEL`, `PROGENITOR_COPILOT_TIMEOUT_SECONDS`, `PROGENITOR_COPILOT_DEFAULT_FLAGS`
- Storage: `PROGENITOR_STATE_DIR`, `PROGENITOR_LOG_DIR`, `PROGENITOR_WORKTREE_DIR`, `PROGENITOR_PROMPTS_DIR`

### Worktrees
- Worktrees are created per task via `create_worktree`.
- Default path is `PROGENITOR_WORKTREE_DIR` (commonly `<repo>/worktrees`).
- Each task runs in an isolated branch/worktree to reduce cross-task contamination.

### Sandbox execution
- DeepAgent uses `LocalShellBackend` with `virtual_mode=False` and `inherit_env=True`.
- Copilot ACP directory access can be constrained with `PROGENITOR_COPILOT_ACP_ALLOWED_DIRS`.
- In containerized deployments, bind mounts and allowed dirs should define the effective execution boundary.

### Required permissions
Builder Copilot needs:
- repository read/write in the active worktree
- ability to commit and push branches
- ability to create PRs via GitHub CLI (`GH_TOKEN` or equivalent)
- authenticated Copilot CLI session/token
- Azure OpenAI access for planning/builder model calls

## 7. A2A Protocol Invocation
Builder Copilot is exposed through the A2A server app in `app/agents/builder_copilot/agent.py` and publishes:
- `GET /.well-known/agent.json`
- `GET /health`
- A2A task endpoint (default app route)

### Invocation model
1. Discover card via `/.well-known/agent.json`.
2. Send an A2A task/message request with user text and optional metadata context.
3. Receive task status updates and a final artifact containing the result text.

### Example JSON request
Use this as a representative A2A JSON-RPC task payload:

```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "method": "tasks/send",
  "params": {
    "id": "task-builder-001",
    "message": {
      "role": "user",
      "parts": [
        {
          "kind": "text",
          "text": "Add retry logic to the Azure Responses client and include tests."
        }
      ],
      "metadata": {
        "repo": "tamaw/progenitor",
        "base_branch": "main",
        "priority": "normal"
      }
    }
  }
}
```

### Example execution context
Execution context is passed through message metadata and typically includes:

```json
{
  "repo": "tamaw/progenitor",
  "base_branch": "main",
  "target_paths": ["app/progenitor", "tests/progenitor"],
  "validation": {
    "test": "pytest tests/progenitor/ -v",
    "lint": "ruff check app/"
  },
  "constraints": {
    "smallest_diff": true,
    "must_open_pr": true,
    "no_deploy": true
  }
}
```

## 8. Usage Examples

### Adding a feature
1. Submit feature request with scope and acceptance criteria.
2. **plan**: create worktree, inspect modules/tests, define commands.
3. **build**: implement feature via `run_copilot_build`.
4. **test**: run focused tests, fix failures.
5. **lint**: run lint/format checks.
6. **pr**: commit, open PR, return URL and summary.

### Fixing a bug
1. Submit bug report with reproduction details.
2. **plan**: locate failing path and likely tests.
3. **build**: patch root cause with minimal change.
4. **test**: run regression test first, then related suite.
5. **lint**: validate style/static checks.
6. **pr**: include root-cause summary and validation notes.

### Refactoring
1. Submit refactor goal and non-functional constraints.
2. **plan**: map impacted files and risk areas.
3. **build**: perform incremental refactor in worktree.
4. **test**: run targeted suite for behavioral parity.
5. **lint**: enforce formatting/static checks.
6. **pr**: document refactor scope and safety checks.

### Updating documentation
1. Submit docs update request and target audience.
2. **plan**: identify source files and required technical references.
3. **build**: edit docs with minimal, consistent changes.
4. **test/lint**: run docs lint/tests when available; otherwise run repo-required checks.
5. **pr**: include concise summary of updated docs and rationale.

## 9. Architecture

### Orchestrator behavior
- `BuilderCopilotService` initializes config, workspace, planner client, and DeepAgent graph.
- Every request gets a preflight structured plan (`BuilderCopilotClient.plan_step`) before execution.
- Runtime returns concise text through A2A task artifacts and status events.

### Tool layer
Custom tools are defined in `app/agents/builder_copilot/tools.py`:
- planning/workspace: `summarize_request`, `create_worktree`
- build: `run_copilot_build`, `build_progenitor`
- validation/search: `run_worktree_command`, `python_search`
- delivery: `commit_changes`, `create_pull_request`

### Skills system
- Skills are loaded from `app/agents/builder_copilot/skills`.
- Available skill IDs: `plan`, `build`, `test`, `lint`, `pr`, `python-search`.
- Skills enforce execution order and concise output conventions.

### Worktree isolation
- Each task is mapped to a dedicated git worktree/branch.
- Commands and builds are executed inside that worktree path.
- This supports safe parallel work and predictable diffs.

### Validation gates
- Test and lint are expected before PR by default.
- PR step depends on commit + push + PR creation.
- Middleware provides redaction, audit logging, and basic metrics for observability.

## 10. Conventions and Guardrails
- Plan first, then build.
- Read/search before editing unfamiliar code.
- Prefer smallest possible safe diff.
- Keep responses concise and operational.
- Run test and lint before PR unless explicitly waived.
- Commit before opening PR.
- Do not merge PRs automatically.
- Do not perform deployments unless explicitly requested by a human.

## 11. Limitations
- Optimized for repository-scoped build tasks, not broad product orchestration.
- Validation quality depends on available project tests/lint commands.
- Non-Python code discovery is available but Python search is the primary fast-path skill.
- Runtime safety boundaries depend on host/container configuration and allowed directory settings.

## 12. Contributing
1. Implement Builder Copilot changes under `app/agents/builder_copilot/`.
2. Keep edits minimal and aligned with existing patterns.
3. Add/update tests in `tests/agents/builder_copilot/` when behavior changes.
4. Validate locally before PR:
   - `pytest tests/agents/builder_copilot/ -v`
   - `ruff check app/`
   - `mypy app/`
5. Open PR with summary, validation commands, and any constraints/waivers.

