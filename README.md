# Builder Copilot

Builder Copilot is a repository-change execution agent that turns natural-language requests into small, reviewable pull requests. It operates as an Agent-to-Agent (A2A) service and focuses on safe, structured delivery: **plan -> build -> test -> lint -> pr**.

## Overview

Builder Copilot exists to automate code changes while preserving engineering discipline:
- understands repository context before editing
- works in isolated git worktrees
- uses explicit validation steps before proposing a PR
- returns concise, machine-consumable results to upstream orchestrators

In a larger multi-agent system, Builder Copilot is the specialized implementation worker for code-change tasks.

## Core Capabilities

- **Repository-aware planning**
  - normalizes incoming requests
  - inspects code before edits
  - produces an actionable preflight plan

- **Isolated worktrees**
  - creates one worktree per task
  - keeps main branch and concurrent tasks isolated
  - supports safe cleanup and parallel development

- **Structured workflow**
  - enforces the sequence: `plan -> build -> test -> lint -> pr`
  - supports explicit user waivers for skipped phases
  - returns final execution summary with validation evidence

- **Tool orchestration with safety constraints**
  - uses purpose-built tools for search, build, command execution, commit, and PR
  - favors narrow test/lint commands first
  - requires commit before PR creation

## Available Skills

### 1) `plan`
**Purpose**
- Define scope, create/confirm worktree, and prepare validation strategy before editing.

**When it is used**
- At the start of every new change request.

**Tools leveraged**
- `summarize_request`
- `create_worktree`
- `python_search`
- `run_worktree_command`

**Example usage**
```text
Use skill: plan
Request: Add audit logging to token refresh flow.
Expected output: work_item_id, worktree path, impacted files, test/lint commands.
```

---

### 2) `build` (`run_copilot_build` / `build_progenitor`)
**Purpose**
- Implement requested code changes with minimal, targeted edits.

**When it is used**
- After planning confirms scope and files.

**Tools leveraged**
- `run_copilot_build` (general implementation)
- `build_progenitor` (scoped implementation for progenitor-specific changes)
- `run_worktree_command` (build-time checks)

**Example usage**
```text
Use skill: build
Tool: run_copilot_build
Prompt: Implement retry with exponential backoff in the API client and update related tests.
```

```text
Use skill: build
Tool: build_progenitor
Prompt: Update progenitor decision routing to include lint gate before PR.
```

---

### 3) `test`
**Purpose**
- Validate behavior with focused test commands.

**When it is used**
- Immediately after build changes and before lint/PR.

**Tools leveraged**
- `run_worktree_command`
- `execute` (skill-level execution utility)

**Example usage**
```bash
run_worktree_command(worktree_path, "pytest tests/module_x -q")
```

---

### 4) `lint`
**Purpose**
- Enforce code quality and style checks expected by the repository.

**When it is used**
- After tests pass and before PR creation.

**Tools leveraged**
- `run_worktree_command`
- `execute`

**Example usage**
```bash
run_worktree_command(worktree_path, "ruff check app/ && mypy app/")
```

---

### 5) `pr`
**Purpose**
- Commit validated changes, push branch, and open pull request.

**When it is used**
- Only after build/test/lint are complete (or explicitly waived).

**Tools leveraged**
- `commit_changes`
- `create_pull_request`
- `run_worktree_command`

**Example usage**
```text
Use skill: pr
commit_changes(message="feat: add retry logic for token refresh")
create_pull_request(title="feat: add retry logic", body="Summary + test notes")
```

---

### 6) `python-search`
**Purpose**
- Quickly locate symbols, imports, tests, and references in Python code.

**When it is used**
- During planning and before modifying unfamiliar code paths.

**Tools leveraged**
- `python_search`
- `grep`
- `execute`

**Example usage**
```bash
python_search(worktree_path, query="class TokenRefresher", glob_pattern="*.py")
```

## Configuration

### Environment assumptions
- A valid git repository is available for worktree creation.
- GitHub Copilot CLI is installed and authenticated.
- GitHub CLI is installed and authenticated for PR creation.
- LLM endpoint/model settings are configured via environment variables.

### Worktree structure
Builder Copilot creates task-scoped worktrees under the configured worktree root.

Example naming shape:
```text
worktrees/
  <work_item_id>_<timestamp>/
```

### Branch naming conventions
When not explicitly provided, branches follow a generated pattern:
```text
progenitor/<work_item_id>/<short-random-suffix>
```

### Test and lint expectations
- Run the **smallest relevant** test command first.
- Run lint/format/type checks expected by the target repository.
- Resolve failures before opening PRs unless the request explicitly waives validation.

## A2A Protocol Invocation

Builder Copilot is invoked through A2A JSON-RPC using `message/send`.

### Example request payload
```json
{
  "jsonrpc": "2.0",
  "id": "req-123",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "message_id": "msg-001",
      "role": "user",
      "parts": [
        {
          "kind": "text",
          "text": "Add request ID propagation middleware and tests."
        }
      ]
    },
    "metadata": {
      "base_branch": "main",
      "priority": "normal"
    }
  }
}
```

### Example response structure
```json
{
  "jsonrpc": "2.0",
  "id": "req-123",
  "result": {
    "message": {
      "kind": "message",
      "message_id": "msg-resp-001",
      "role": "agent",
      "parts": [
        {
          "kind": "text",
          "text": "Completed plan/build/test/lint/pr workflow. PR: https://github.com/org/repo/pull/123"
        }
      ]
    },
    "metadata": {}
  }
}
```

## Usage Examples

### Example: Add a new feature
```text
Request: Add an endpoint to export project metrics as CSV.
Flow: plan -> build -> test -> lint -> pr
Outcome: new endpoint, tests, lint-clean PR.
```

### Example: Fix a bug
```text
Request: Fix race condition in session refresh logic.
Flow: plan (reproduce + scope) -> build (minimal patch) -> test (targeted regression) -> lint -> pr
Outcome: bug fix with regression coverage.
```

### Example: Add tests only
```text
Request: Add tests for existing rate-limit middleware; no production code changes.
Flow: plan -> build (tests only) -> test -> lint -> pr
Outcome: test-only PR with clear validation notes.
```

## Architecture

### High-level flow
```text
A2A caller
   |
   v
Builder Copilot Service
   |
   +--> Preflight planner (reasoned plan)
   |
   v
DeepAgent runtime
   |
   +--> Skill: plan
   +--> Skill: build
   +--> Skill: test
   +--> Skill: lint
   +--> Skill: pr
   |
   v
Tool runtime (worktree + copilot + commands + git/gh)
   |
   v
Result artifact + final status
```

### Component responsibilities
- **A2A Executor**: receives requests, emits working/completed/failed status events.
- **Planner Client**: generates structured preflight plan and risk summary.
- **DeepAgent Graph**: executes ordered skill workflow.
- **Tool Runtime**: performs worktree ops, build execution, shell validation, commits, PR creation.
- **Middleware**: audit logging, metrics, and secret redaction.

### Skill layering model
- **Layer 1 - Planning**: request normalization, scope, code discovery.
- **Layer 2 - Implementation**: code edits via Copilot tools.
- **Layer 3 - Validation**: tests + lint/quality gates.
- **Layer 4 - Delivery**: commit, push, PR creation, final summary.

## Development Guidelines

### Conventions
- Keep edits minimal and scoped to the requested outcome.
- Prefer existing patterns/helpers over introducing new abstractions.
- Keep commit and PR messages concise and technical.

### Safety rules
- Do not skip test/lint unless explicitly requested.
- Do not open PRs without a commit.
- Use isolated worktrees for all mutation tasks.
- Surface command failures directly; avoid silent fallbacks.

### Validation steps
1. Confirm changed files match planned scope.
2. Run focused tests.
3. Run lint/type/format checks required by the repo.
4. Summarize results in PR body (tests run, risks, follow-ups).

## Contributing / Extending Skills

To extend Builder Copilot:
1. Add or update skill definitions under the skills directory.
2. Map skill behavior to explicit tools (least privilege).
3. Keep skill prompts action-oriented and deterministic.
4. Add/update tests for agent card, tools, and flow behavior.
5. Validate end-to-end with a real worktree run before shipping.

Recommended extension pattern:
```text
new skill -> narrow tool access -> explicit entry/exit criteria -> tests -> rollout
```
