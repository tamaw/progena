# Builder Copilot

## 1) Title and Overview

Builder Copilot is the repository mutation agent in the Progenitor system.  
It receives scoped implementation requests, executes them in an isolated git worktree, validates quality gates, and prepares pull requests.  
Its purpose is to turn high-level build intent into auditable code changes without mutating the primary working branch directly.

## 2) Core Capabilities

- **Plan repository changes** from natural language goals into concrete edit/test steps.
- **Create isolated worktrees** per task to prevent cross-task contamination.
- **Run code search and inspection** before editing to minimize incorrect assumptions.
- **Implement changes via Copilot (ACP mode)** for structured, tool-driven code edits.
- **Run tests and lint** to validate behavior and quality before PR creation.
- **Create pull requests** with a clear summary of modifications and validation status.

## 3) Available Skills

### `plan`
- **When to use**: Before implementation, or when requirements need to be translated into actionable tasks.
- **Tools**: Repository search tools, file inspection tools, planning/output formatting.
- **Typical workflow**:
  1. Parse request constraints and expected output.
  2. Inspect relevant code paths.
  3. Produce a step-by-step change plan with validation gates.

### `build` (`run_copilot_build` / `build_progenitor`)
- **When to use**: To implement code changes after planning is complete.
- **Tools**: Copilot CLI in ACP mode, git worktree operations, edit/apply patch tooling.
- **Typical workflow**:
  1. Create or select isolated worktree/branch.
  2. Apply planned edits using Copilot-driven implementation.
  3. Collect a deterministic diff for review.

### `test`
- **When to use**: After build changes and before PR creation.
- **Tools**: Project test runner(s), targeted and/or full-suite execution.
- **Typical workflow**:
  1. Run impacted tests first.
  2. Run broader regression tests as configured.
  3. Report pass/fail with failing cases and next actions.

### `lint`
- **When to use**: After build/test, prior to PR creation.
- **Tools**: Repository lint/type-check commands (language-specific).
- **Typical workflow**:
  1. Run lint/type checks in worktree.
  2. Fix violations introduced by the change.
  3. Re-run to confirm clean status.

### `pr`
- **When to use**: Once build, test, and lint are complete and green (or exceptions are documented).
- **Tools**: git metadata commands, GitHub CLI/API for PR creation.
- **Typical workflow**:
  1. Stage final branch state.
  2. Generate title/body from change summary and validation output.
  3. Open PR and return URL plus checklist status.

### `python-search`
- **When to use**: For fast symbol- and usage-level discovery in Python-heavy changes.
- **Tools**: `rg`/glob-based code search, file viewers, structural inspection patterns.
- **Typical workflow**:
  1. Locate target modules, call sites, and tests.
  2. Confirm contracts and side effects.
  3. Feed findings back into `plan` and `build`.

## 4) Configuration

### Worktree behavior
- One task maps to one isolated worktree.
- Worktree is treated as ephemeral implementation context.
- Primary branch is not directly edited during build execution.

### Branch naming
- Branches should be deterministic and task-scoped.
- Recommended pattern: `<scope>/<short-task-slug>-<id>`.
- Names should encode intent (feature/fix/refactor) and traceability.

### Test/lint requirements
- Tests and lint checks are required quality gates before PR.
- If any gate fails, Builder Copilot reports failure details and blocks PR until resolved or explicitly waived.
- Gate output is summarized in machine-readable and human-readable form.

### Environment expectations
- Git, test/lint toolchain, and Copilot CLI must be available in execution context.
- Repository must be in a state where a worktree/branch can be created.
- Credentials for PR creation must be configured in the runtime environment.

## 5) A2A Protocol Invocation

Builder Copilot is invoked through an Agent-to-Agent request where the caller supplies task intent, constraints, and expected lifecycle behavior.

### Example A2A request

```json
{
  "jsonrpc": "2.0",
  "id": "req-42",
  "method": "tasks.send",
  "params": {
    "agent": "builder-copilot",
    "skill": "build_progenitor",
    "input": {
      "goal": "Add health endpoint with test coverage",
      "constraints": [
        "no breaking API changes",
        "run tests and lint before PR"
      ],
      "lifecycle": ["plan", "build", "test", "lint", "pr"]
    }
  }
}
```

### Expected lifecycle

1. **plan**: Analyze request and produce execution plan.
2. **build**: Implement in isolated worktree.
3. **test**: Run required tests and collect results.
4. **lint**: Run lint/type checks and resolve violations.
5. **pr**: Open pull request with summary and gate status.

## 6) Usage Examples

### Example: Add new endpoint

**Prompt**
```text
Add a new GET /status endpoint that returns service version and uptime.
Include tests and open a PR.
```

**Expected actions**
1. Plan endpoint contract and test cases.
2. Implement handler + routing in worktree.
3. Run targeted and regression tests.
4. Run lint/type checks.
5. Create PR with endpoint behavior summary.

### Example: Fix failing test

**Prompt**
```text
Investigate and fix the failing auth token expiration test, then open a PR.
```

**Expected actions**
1. Reproduce failure in isolated worktree.
2. Locate defect using code/test inspection.
3. Implement minimal fix and update/add tests if needed.
4. Re-run test/lint gates.
5. Create PR with root cause and fix notes.

### Example: Refactor module

**Prompt**
```text
Refactor the scheduling module to reduce duplication without changing behavior.
Run full tests and lint before PR.
```

**Expected actions**
1. Plan safe refactor boundaries and invariants.
2. Apply behavior-preserving edits in worktree.
3. Run full validation suite.
4. Create PR documenting non-functional refactor scope.

## 7) Architecture

### High-level architecture
- **Caller agent/system** sends structured task to Builder Copilot via A2A.
- **Builder Copilot** executes lifecycle steps and reports state transitions.
- **Git provider layer** receives branch/PR operations after validation passes.

### Tool orchestration
- Search/inspection tools run first to establish context.
- Editing tools run only after a plan is accepted.
- Validation tools (test/lint) gate PR creation.
- Output includes task summary, diff metadata, and gate results.

### Skills system
- Skills are explicit execution modes (`plan`, `build`, `test`, `lint`, `pr`, `python-search`).
- Each skill has defined entry criteria, toolset, and completion criteria.
- Skills compose into the standard lifecycle for predictable automation.

### Subagent usage (`task` tool)
- Builder Copilot can delegate bounded subtasks to specialized subagents via the `task` tool.
- Typical uses: large test runs, focused repository exploration, or review checks.
- Delegation remains traceable to the parent task and lifecycle stage.

### Worktree isolation model
- Every build task executes in an isolated git worktree and branch.
- Isolation enables concurrent tasks and simplifies rollback/cleanup.
- PRs originate from isolated branches, preserving mainline stability.
