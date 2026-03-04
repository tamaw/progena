# Builder Copilot

A **minimal, composable AI agent** that implements the Last Algorithm pattern to plan, build, test, lint, and open pull requests for repository changes.

## Table of Contents

1. [Purpose](#purpose)
2. [Capabilities](#capabilities)
3. [Available Skills](#available-skills)
4. [Configuration](#configuration)
5. [A2A Protocol Invocation](#a2a-protocol-invocation)
6. [Usage Examples](#usage-examples)
7. [Architecture](#architecture)
8. [Conventions & Best Practices](#conventions--best-practices)

---

## Purpose

Builder Copilot is an **autonomous build agent** designed to:

- **Receive natural language requests** for code changes, feature additions, bug fixes, or refactoring work
- **Plan changes systematically** using the Last Algorithm pattern (Observe → Think → Plan → Build → Execute → Validate → Verify → Learn)
- **Isolate changes in git worktrees** to safely implement and test changes in parallel
- **Invoke GitHub Copilot** (via the Copilot CLI) to assist with code generation and modifications
- **Validate work** with targeted test and lint commands
- **Ship work** by committing and opening pull requests

It is **not** a monolithic code generation engine. Instead, it:
- Works **incrementally** through a loop that plans, builds, validates, and learns
- Respects **repository conventions** (test structure, lint rules, branch strategy)
- Operates **within git worktrees** to isolate work and prevent main-branch pollution
- Integrates **tightly with GitHub Copilot CLI** to provide focused, scoped code changes
- Maintains **clear separation of concerns** via discrete skills (plan, build, test, lint, pr)

**Where it fits:**

Builder Copilot is deployed as an **A2A (Agent-to-Agent) server** on port 8108. It is invoked by:
- The main **Progenitor agent** (conversational dispatcher) when users request build/upgrade tasks
- External systems that want to submit structured build requests via A2A protocol
- Development workflows where CI/CD pipelines need to auto-generate code changes

---

## Capabilities

### High-Level Summary

- **Planning**: Normalizes requests, creates isolated worktrees, inspects target code
- **Building**: Invokes GitHub Copilot with scoped prompts (full repo or specific modules like `app/progenitor`)
- **Testing**: Runs focused test commands and captures failures
- **Linting**: Enforces code quality (ruff, mypy, black, etc.)
- **PR Creation**: Commits changes, pushes worktree branches, opens GitHub PRs
- **Code Search**: Searches Python files quickly before editing
- **Validation Loop**: Re-plans and rebuilds if validation fails

### Supported Task Types

- **Feature additions**: Add new functions, modules, or endpoints
- **Bug fixes**: Locate issues, implement fixes, validate with tests
- **Refactoring**: Reorganize code, extract functions, improve interfaces
- **Tests/Documentation**: Write or update tests, docstrings, README sections
- **Module-scoped work**: Focus Copilot changes to specific directories (e.g., `app/progenitor`)

---

## Available Skills

Builder Copilot organizes work into discrete **skills**, each with specific allowed tools and responsibilities. The agent selects skills based on the task phase.

### Skill: `plan`

**When to use:** Start of every task. Normalize the request, create a worktree, inspect target code.

**Allowed tools:** `summarize_request`, `create_worktree`, `python_search`, `run_worktree_command`

**Workflow:**
1. Normalize the user request and generate a stable work item ID
2. Create an isolated git worktree from the current main branch
3. Search for relevant source files or references (if needed)
4. Document the exact scope: which files will change, which commands will validate

**Example:**
```
User request: "Add a retry decorator to app/utils/retry.py"

Plan output:
- work_item_id: "add-retry-decorator-a1b2c3d4"
- worktree_path: "/worktrees/add-retry-decorator-a1b2c3d4"
- target_files: ["app/utils/retry.py", "tests/test_retry.py"]
- test_command: "pytest tests/test_retry.py -v"
- lint_command: "ruff check app/utils/retry.py"
```

### Skill: `build`

**When to use:** After planning. Implement changes using GitHub Copilot.

**Tools involved:**
- `run_copilot_build` — Run Copilot in ACP (Advanced Code Personalization) mode with a prompt and target path
- `build_progenitor` — Convenience wrapper scoped to `app/progenitor` directory
- `run_worktree_command` — Run build/install steps (e.g., `pip install -r requirements.txt`)
- `python_search` — Inspect code before editing

**Key behaviors:**
- **Scoped prompts**: Copilot receives context-aware prompts that specify exactly what to change
- **ACP mode**: Focuses Copilot to a single directory (or the entire worktree if no target)
- **Incremental**: Can run multiple Copilot invocations; each refines the previous result
- **Fallback**: If validation fails, can rebuild with more specific instructions

**Example:**
```
Prompt (to Copilot):
"In tests/test_retry.py, add a test case for exponential backoff with max_retries=5.
Use pytest.fixture and mock.patch. Test should verify retry counts and delays."

build_progenitor usage:
- work_item_id: "add-retry-tests-abc123"
- worktree_path: "/worktrees/add-retry-tests-abc123"
- prompt: "..."
- target_path: "app/progenitor" (optional, scopes Copilot to this directory)
```

### Skill: `test`

**When to use:** After building, before lint. Validate changes with targeted tests.

**Allowed tools:** `run_worktree_command`

**Workflow:**
1. Identify the smallest relevant test command (e.g., single test file, not the full suite)
2. Run the test in the worktree
3. Capture output and failures clearly
4. If tests fail, return to **build** skill with failure details for Copilot to fix

**Example:**
```
command: "pytest tests/test_retry.py -v"

Output:
PASSED tests/test_retry.py::test_retry_with_exponential_backoff
FAILED tests/test_retry.py::test_max_retries_honored

→ If failed, builder rebuilds with: "Fix the test_max_retries_honored test case..."
```

### Skill: `lint`

**When to use:** After tests pass, before opening PR. Enforce code quality.

**Allowed tools:** `run_worktree_command`

**Workflow:**
1. Run the repo's narrowest lint command (e.g., `ruff check app/utils/retry.py`)
2. Capture lint errors and formatting issues
3. If issues found, can either:
   - Fix programmatically (e.g., `black --line-length=100 app/utils/retry.py`)
   - Return to **build** skill for Copilot to fix contextually

**Example:**
```
command: "ruff check app/utils/retry.py"

Output:
app/utils/retry.py:15:1: F401 'unused_import' imported but unused

→ Fix: Remove unused import or return to build
```

### Skill: `pr`

**When to use:** After build, test, and lint pass. Commit and open a PR.

**Allowed tools:** `commit_changes`, `create_pull_request`, `run_worktree_command`

**Workflow:**
1. Confirm there are changes to commit (run `git status`)
2. Create a clear, concise commit message (following repo conventions)
3. Commit all changes
4. Push the worktree branch to the remote
5. Open a GitHub PR with title, body, and test notes
6. Return the PR URL as task completion

**Example:**
```
commit_changes(
  worktree_path="/worktrees/add-retry-decorator-a1b2c3d4",
  message="feat: Add exponential backoff retry decorator\n\nImplements retry.py with configurable max_retries and backoff. Includes comprehensive tests and type hints."
)

create_pull_request(
  worktree_path="/worktrees/add-retry-decorator-a1b2c3d4",
  title="Add exponential backoff retry decorator",
  body="Implements a configurable retry decorator with exponential backoff...",
  base="main"
)

Output:
{
  "success": true,
  "url": "https://github.com/owner/repo/pull/42"
}
```

### Skill: `python-search`

**When to use:** Before editing unfamiliar code. Locate symbols, imports, test files, or references.

**Allowed tools:** `python_search`, `grep`, `run_worktree_command`

**Workflow:**
1. Use targeted `python_search` queries (ripgrep under the hood)
2. Search for function definitions, class locations, imports, usages
3. Inspect test files and existing patterns
4. Return findings to inform build strategy

**Example:**
```
python_search(
  worktree_path="/worktrees/add-retry-decorator-a1b2c3d4",
  query="def retry",
  glob_pattern="*.py"
)

Output:
app/utils/retry.py:5: def retry(func):
tests/test_retry.py:1: def test_retry_basic():
```

---

## Configuration

### Environment Setup

Builder Copilot reads configuration from environment variables and `.env` files. Key settings:

#### LLM Configuration

```bash
# Azure OpenAI (primary provider)
AZURE_OPENAI_BASE_URL=https://<resource>.openai.azure.com/openai/v1
AZURE_OPENAI_API_KEY=<your-api-key>
PROGENITOR_MODEL_DEFAULT=gpt-5.2-chat
PROGENITOR_MODEL_PLANNER=gpt-5.1         # Optional: override for planning tasks
PROGENITOR_MODEL_BUILDER=gpt-5.3-codex   # Optional: override for build tasks
```

#### GitHub Copilot CLI Configuration

```bash
# Path to copilot binary
PROGENITOR_COPILOT_BIN=copilot

# Timeout for Copilot invocations (seconds)
PROGENITOR_COPILOT_TIMEOUT_SECONDS=1200

# Default flags passed to copilot command
PROGENITOR_COPILOT_DEFAULT_FLAGS=--yolo

# Model for Copilot (codex or latest Copilot model)
PROGENITOR_COPILOT_MODEL=gpt-5.3-codex

# Directories allowed for ACP (Advanced Code Personalization)
PROGENITOR_COPILOT_ACP_ALLOWED_DIRS=app/progenitor,app/agents,app/backend
```

#### Runtime Configuration

```bash
# Log level: debug, info, warn, error
PROGENITOR_LOG_LEVEL=info

# Log format: text or json
PROGENITOR_LOG_FORMAT=text

# Git user (for commits)
GIT_AUTHOR_NAME="Builder Copilot"
GIT_AUTHOR_EMAIL="copilot@example.com"
```

#### Storage Paths

```bash
# Project root (cloned repo or workspace mount)
PROGENITOR_PROJECT_ROOT=/workspace

# Git repository URL (optional, used if workspace is empty)
PROGENITOR_REPO_URL=https://github.com/owner/repo.git

# Directories
PROGENITOR_STATE_DIR=/data/state
PROGENITOR_LOG_DIR=/logs
PROGENITOR_WORKTREE_DIR=/worktrees
PROGENITOR_PROMPTS_DIR=/prompts
```

### Repository Assumptions

Builder Copilot expects repositories to follow common Python/Node.js conventions:

- **Main branch**: `main` or `master` (detected automatically)
- **Python projects**: `pyproject.toml`, `requirements.txt`, `pytest.ini`
- **Linting**: `ruff`, `mypy`, or `black` (configurable per repo)
- **Testing**: `pytest` (configurable)
- **Git**: Standard GitHub workflow (main → feature branches → PRs)

### Worktree Model

Each task gets an **isolated git worktree**:

```
/worktrees/
├── add-retry-decorator-a1b2c3d4/       # Isolated workspace
│   ├── .git                             # Worktree-specific git
│   ├── app/
│   ├── tests/
│   ├── requirements.txt
│   └── ...
├── fix-caching-bug-x9y8z7w6/
└── refactor-state-manager-m1n2o3p4/
```

**Benefits:**
- Safe parallel work (no main branch pollution)
- Clean rollback (delete worktree)
- Tests run in isolation
- Easy inspection and debugging

### Branch Strategy

1. **Start:** Worktree is created from `main` (or the default branch)
2. **Build:** Changes committed to a feature branch (auto-named, e.g., `builder/add-retry-decorator-a1b2c3d4`)
3. **Push:** Worktree branch is pushed to remote
4. **PR:** GitHub PR is opened against `main` with descriptive title and body
5. **Cleanup:** Worktree deleted after PR is created (optionally after merge)

---

## A2A Protocol Invocation

Builder Copilot is deployed as an **A2A (Agent-to-Agent) server** using the `a2a-sdk`. It accepts structured JSON requests and returns results via HTTP.

### Server Details

- **Port**: 8108
- **Protocol**: A2A JSON-RPC (via `a2a-sdk`)
- **Base URL**: `http://localhost:8108`

### Request/Response Format

#### Request Structure

```json
{
  "type": "execute_skill",
  "skill": "plan",
  "context_json": {
    "request": "Add a retry decorator to app/utils/retry.py",
    "work_item_id": "add-retry-decorator-a1b2c3d4",
    "repository": "owner/repo",
    "branch": "main"
  },
  "parameters": {
    "request": "Add a retry decorator to app/utils/retry.py"
  }
}
```

#### Response Structure (Success)

```json
{
  "task_id": "task-uuid-1234",
  "status": "COMPLETED",
  "skill": "plan",
  "output": {
    "work_item_id": "add-retry-decorator-a1b2c3d4",
    "normalized_request": "Add a retry decorator to app/utils/retry.py",
    "worktree_path": "/worktrees/add-retry-decorator-a1b2c3d4",
    "branch": "builder/add-retry-decorator-a1b2c3d4",
    "target_files": ["app/utils/retry.py", "tests/test_retry.py"],
    "test_command": "pytest tests/test_retry.py -v",
    "lint_command": "ruff check app/utils/retry.py && mypy app/utils/retry.py"
  },
  "artifacts": []
}
```

#### Response Structure (Failure)

```json
{
  "task_id": "task-uuid-1234",
  "status": "FAILED",
  "skill": "plan",
  "error": {
    "message": "Could not determine test command for module",
    "code": "PLAN_FAILED"
  },
  "artifacts": []
}
```

### Multi-Step Workflow via A2A

A complete build workflow would invoke multiple skills sequentially:

```json
// Step 1: Plan
POST /v1/execute
{
  "skill": "plan",
  "parameters": {
    "request": "Add retry decorator to app/utils/retry.py"
  }
}
→ Returns: work_item_id, worktree_path, plan details

// Step 2: Build
POST /v1/execute
{
  "skill": "build",
  "context": {
    "work_item_id": "add-retry-decorator-a1b2c3d4",
    "worktree_path": "/worktrees/add-retry-decorator-a1b2c3d4"
  },
  "parameters": {
    "prompt": "Add exponential backoff retry decorator with max_retries parameter",
    "target_path": null
  }
}
→ Returns: success, summary, diff, stdout, stderr

// Step 3: Test
POST /v1/execute
{
  "skill": "test",
  "context": {
    "worktree_path": "/worktrees/add-retry-decorator-a1b2c3d4"
  },
  "parameters": {
    "command": "pytest tests/test_retry.py -v"
  }
}
→ Returns: success, stdout, stderr, returncode

// Step 4: Lint
POST /v1/execute
{
  "skill": "lint",
  "context": {
    "worktree_path": "/worktrees/add-retry-decorator-a1b2c3d4"
  },
  "parameters": {
    "command": "ruff check app/utils/retry.py"
  }
}
→ Returns: success, stdout, stderr, returncode

// Step 5: PR
POST /v1/execute
{
  "skill": "pr",
  "context": {
    "worktree_path": "/worktrees/add-retry-decorator-a1b2c3d4"
  },
  "parameters": {
    "title": "feat: Add exponential backoff retry decorator",
    "body": "Implements retry.py with configurable max_retries and backoff...",
    "base": "main"
  }
}
→ Returns: success, url, error
```

### Required Fields for Each Skill

| Skill | Required Context | Required Parameters | Returns |
|-------|------------------|---------------------|---------|
| `plan` | `request` | `request`, `context_json` (optional) | `work_item_id`, `worktree_path`, `plan` |
| `build` | `work_item_id`, `worktree_path` | `prompt`, `target_path` (optional) | `success`, `summary`, `diff`, `stdout`, `stderr` |
| `test` | `worktree_path` | `command`, `timeout_seconds` (optional) | `success`, `stdout`, `stderr`, `returncode` |
| `lint` | `worktree_path` | `command`, `timeout_seconds` (optional) | `success`, `stdout`, `stderr`, `returncode` |
| `pr` | `worktree_path` | `title`, `body`, `base` | `success`, `url` (or `error`) |
| `python-search` | `worktree_path` | `query`, `glob_pattern` (optional) | `stdout`, `stderr`, `returncode` |

---

## Usage Examples

### Example 1: Add a New Feature

**Scenario:** User asks Builder Copilot to "Add a caching decorator to `app/utils/cache.py`"

#### Step 1: Plan

```bash
# Agent normalizes request and creates a worktree
Request:
  "Add a caching decorator to app/utils/cache.py"

Output:
  work_item_id: "add-caching-decorator-x9y8z7w6"
  worktree_path: "/worktrees/add-caching-decorator-x9y8z7w6"
  branch: "builder/add-caching-decorator-x9y8z7w6"

# Agent inspects repository structure
python_search(
  worktree_path="/worktrees/add-caching-decorator-x9y8z7w6",
  query="def cache",
  glob_pattern="*.py"
)
→ Finds existing decorator patterns to follow
```

#### Step 2: Build

```bash
# Agent invokes Copilot with scoped prompt
run_copilot_build(
  work_item_id="add-caching-decorator-x9y8z7w6",
  worktree_path="/worktrees/add-caching-decorator-x9y8z7w6",
  prompt="Add a caching decorator with LRU cache, TTL support, and cache invalidation.
          Follow the existing @retry decorator pattern.
          Place implementation in app/utils/cache.py.
          Add tests in tests/test_cache.py.",
  target_path=null
)

Output:
  success: true
  summary: "Created @cache decorator with LRU/TTL, tests for eviction and invalidation"
  diff: (files changed)
```

#### Step 3: Test

```bash
# Agent runs focused tests
run_worktree_command(
  worktree_path="/worktrees/add-caching-decorator-x9y8z7w6",
  command="pytest tests/test_cache.py -v"
)

Output:
  success: true
  returncode: 0
  stdout: "PASSED tests/test_cache.py::test_cache_hit
           PASSED tests/test_cache.py::test_cache_miss
           PASSED tests/test_cache.py::test_ttl_expiration
           ..."
```

#### Step 4: Lint

```bash
# Agent checks code quality
run_worktree_command(
  worktree_path="/worktrees/add-caching-decorator-x9y8z7w6",
  command="ruff check app/utils/cache.py && mypy app/utils/cache.py"
)

Output:
  success: true
  returncode: 0
  stdout: "All files checked successfully"
```

#### Step 5: PR

```bash
# Agent commits and opens PR
commit_changes(
  worktree_path="/worktrees/add-caching-decorator-x9y8z7w6",
  message="feat: Add LRU caching decorator with TTL support

Implements @cache decorator with:
- Configurable LRU cache size
- Time-to-live (TTL) per entry
- Manual cache invalidation
- Type hints and docstrings

Tests: 8 new test cases covering hits, misses, eviction, TTL."
)

create_pull_request(
  worktree_path="/worktrees/add-caching-decorator-x9y8z7w6",
  title="feat: Add LRU caching decorator with TTL support",
  body="Implements a configurable caching decorator for expensive operations.

## Changes
- New `@cache` decorator in `app/utils/cache.py`
- 8 test cases in `tests/test_cache.py`
- Type hints (PEP 484)

## Testing
All tests pass: `pytest tests/test_cache.py -v`

## Lint
All checks pass: `ruff check app/utils/cache.py && mypy app/utils/cache.py`",
  base="main"
)

Output:
  success: true
  url: "https://github.com/owner/repo/pull/123"
```

---

### Example 2: Fix a Bug

**Scenario:** User reports "Fix the timeout calculation in `app/services/api.py` — it's multiplying instead of adding the retry delay"

#### Step 1: Plan + Search

```bash
# Plan normalizes the bug report
Request: "Fix the timeout calculation in app/services/api.py — it's multiplying instead of adding"

# Agent searches for the bug
python_search(
  worktree_path="/worktrees/fix-timeout-bug-m1n2o3p4",
  query="timeout.*retry.*delay",
  glob_pattern="app/services/*.py"
)
→ Finds line: `timeout = retry_count * delay` (incorrect)

# Agent checks tests
python_search(
  worktree_path="/worktrees/fix-timeout-bug-m1n2o3p4",
  query="test.*timeout",
  glob_pattern="tests/test_api.py"
)
→ Finds test_timeout_calculation
```

#### Step 2: Build

```bash
# Agent fixes the bug with Copilot
run_copilot_build(
  work_item_id="fix-timeout-bug-m1n2o3p4",
  worktree_path="/worktrees/fix-timeout-bug-m1n2o3p4",
  prompt="In app/services/api.py, fix the timeout calculation.
          Current: timeout = retry_count * delay
          Correct: timeout = initial_delay + (retry_count * backoff_multiplier)
          Ensure the fix passes existing test_timeout_calculation test.",
  target_path="app/services"
)

Output:
  success: true
  summary: "Fixed timeout calculation from multiplication to addition formula"
```

#### Step 3: Test

```bash
# Run the specific test that was failing
run_worktree_command(
  worktree_path="/worktrees/fix-timeout-bug-m1n2o3p4",
  command="pytest tests/test_api.py::test_timeout_calculation -v"
)

Output:
  success: true
  returncode: 0
  stdout: "PASSED test_timeout_calculation"
```

#### Step 4: Lint & PR

```bash
# Lint passes
run_worktree_command(
  worktree_path="/worktrees/fix-timeout-bug-m1n2o3p4",
  command="ruff check app/services/api.py"
)
→ success: true

# Commit and PR
commit_changes(
  worktree_path="/worktrees/fix-timeout-bug-m1n2o3p4",
  message="fix: Correct timeout calculation in API service

Timeout was incorrectly multiplying retry_count by delay.
Now correctly adds delay + (retry_count * backoff_multiplier).

Fixes test_timeout_calculation test case."
)
```

---

### Example 3: Refactor a Module

**Scenario:** User asks "Refactor `app/progenitor/state/models.py` to split into separate files per domain (conversation, build, schedule)"

#### Step 1: Plan

```bash
Request: "Refactor app/progenitor/state/models.py into separate domain files"

# Agent builds a refactoring plan
python_search(
  worktree_path="/worktrees/refactor-state-m1n2o3p4",
  query="^class.*Model",
  glob_pattern="app/progenitor/state/models.py"
)
→ Finds: ConversationModel, BuildModel, ScheduleModel, etc.

# Agent identifies imports
python_search(
  worktree_path="/worktrees/refactor-state-m1n2o3p4",
  query="from.*models import",
  glob_pattern="app/progenitor/**/*.py"
)
→ Maps all import statements to update
```

#### Step 2: Build (Multi-step)

```bash
# First, create new domain files
run_copilot_build(
  work_item_id="refactor-state-m1n2o3p4",
  worktree_path="/worktrees/refactor-state-m1n2o3p4",
  prompt="Refactor app/progenitor/state/models.py:

1. Extract conversation-related classes to app/progenitor/state/conversation.py
   (ConversationModel, Message, etc.)

2. Extract build-related classes to app/progenitor/state/build.py
   (BuildModel, WorkItem, Decision, etc.)

3. Extract schedule-related classes to app/progenitor/state/schedule.py
   (ScheduleModel, Task, etc.)

4. Keep base classes and utilities in models.py

Maintain all imports and type hints. Do not break existing code.",
  target_path="app/progenitor/state"
)

# Then update imports across the codebase
run_copilot_build(
  work_item_id="refactor-state-m1n2o3p4",
  worktree_path="/worktrees/refactor-state-m1n2o3p4",
  prompt="Update imports in app/progenitor to use new domain modules:
          - from app.progenitor.state.conversation import Message, ConversationModel
          - from app.progenitor.state.build import WorkItem, Decision
          - from app.progenitor.state.schedule import ScheduleModel
          
          Update all 15 Python files that currently import from state/models.py",
  target_path="app/progenitor"
)
```

#### Step 3: Test

```bash
# Run full progenitor test suite
run_worktree_command(
  worktree_path="/worktrees/refactor-state-m1n2o3p4",
  command="pytest tests/progenitor/ -v"
)

Output:
  success: true
  returncode: 0
  stdout: "PASSED 47 tests in tests/progenitor/"
```

#### Step 4: Lint & PR

```bash
# Lint all modified files
run_worktree_command(
  worktree_path="/worktrees/refactor-state-m1n2o3p4",
  command="ruff check app/progenitor/ && mypy app/progenitor/"
)

Output:
  success: true

# Commit and PR
commit_changes(
  worktree_path="/worktrees/refactor-state-m1n2o3p4",
  message="refactor: Split state models by domain

Split monolithic app/progenitor/state/models.py into:
- conversation.py: Message, ConversationModel
- build.py: WorkItem, Decision, BuildModel  
- schedule.py: ScheduleModel, Task

Updated 15 import statements across app/progenitor.
All tests pass: pytest tests/progenitor/ -v

No behavioral changes, pure refactoring."
)
```

---

## Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────┐
│         Builder Copilot (A2A Server on port 8108)           │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │         DeepAgent (LangGraph Loop)                     │ │
│  │  Decide → (Plan|Build|Test|Lint|PR) → Validate        │ │
│  └────────────────────────────────────────────────────────┘ │
│           ↓         ↓         ↓      ↓      ↓               │
│      ┌─────────────────────────────────────────┐            │
│      │      Skill Execution Layer              │            │
│      │  • plan: Planning & scope                │            │
│      │  • build: Copilot invocation            │            │
│      │  • test: Test execution                 │            │
│      │  • lint: Code quality checks            │            │
│      │  • pr: GitHub PR creation               │            │
│      │  • python-search: Code inspection       │            │
│      └─────────────────────────────────────────┘            │
│              ↓              ↓              ↓                 │
│      ┌────────────────┬──────────────┬───────────────┐      │
│      │  Tool Runtime  │  Copilot CLI │  Git/GitHub   │      │
│      │                │              │  (gh, git)    │      │
│      └────────────────┴──────────────┴───────────────┘      │
│              ↓              ↓              ↓                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │     Worktree Isolation & Repository Access         │   │
│  │  /worktrees/<work-item-id>/                        │   │
│  │    - Isolated git branches                         │   │
│  │    - Test/build environment                        │   │
│  │    - Safe parallel execution                       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

#### 1. **Orchestrator (DeepAgent LangGraph)**
   - Accepts A2A requests
   - Routes to appropriate skills
   - Manages state across iterations
   - Implements validation loop (rebuild if validation fails)

#### 2. **Skill System**
   - Discrete, composable units (plan, build, test, lint, pr, python-search)
   - Each skill has explicit allowed tools
   - Skills can call each other (e.g., build → test → lint → pr)
   - Easy to add new skills without modifying core loop

#### 3. **Tool Runtime**
   - **Worktree Manager**: Creates isolated git branches, manages cleanup
   - **Copilot CLI Wrapper**: Invokes GitHub Copilot with scoped prompts and ACP
   - **Command Runner**: Executes shell commands (test, lint, build) in worktrees
   - **GitHub CLI Wrapper**: Opens PRs and manages git remotes

#### 4. **Configuration & Logging**
   - Dataclass-based config (LLMConfig, CopilotConfig, StorageConfig, RuntimeConfig)
   - JSON + text logging with rotation
   - Per-agent log directories

#### 5. **Worktree Isolation**
   - Each task gets a dedicated git worktree
   - Worktrees are isolated from main branch
   - Safe for parallel execution
   - Easy inspection and debugging
   - Cleanup handled by garbage collection or manual deletion

### Data Flow

```
Request (A2A JSON)
    ↓
Agent Service (lazy init)
    ↓
DeepAgent.invoke(skill, context, parameters)
    ↓
Skill Router (plan|build|test|lint|pr|python-search)
    ↓
Tool Runtime (Copilot, Git, Command)
    ↓
Worktree (isolated git + environment)
    ↓
External Services (GitHub API, Azure OpenAI)
    ↓
Response (JSON with status, output, artifacts)
```

### State Management

Builder Copilot maintains **lightweight task state**:

- **Task ID**: Unique identifier for the request
- **Work Item ID**: Stable slug for the feature/fix (e.g., `add-retry-decorator-a1b2c3d4`)
- **Worktree Path**: Path to the isolated git checkout
- **Branch Name**: Feature branch name in the worktree
- **Artifacts**: Links to logs, diffs, PR URLs

State is **not persisted between requests**. Each A2A request is independent. Context must be passed via `context_json` or request parameters for multi-step workflows.

---

## Conventions & Best Practices

### Before Editing Code

1. **Search first**: Use `python_search` to locate existing patterns and imports
2. **Inspect tests**: Understand how the module is tested before modifying
3. **Check configs**: Review any configuration files that might be affected
4. **Read docstrings**: Understand the intended behavior and constraints

### Building Changes

1. **Scope prompts**: Be explicit about what to change and why
2. **Use ACP mode**: Scope Copilot to specific directories (e.g., `app/progenitor`) to avoid unintended changes
3. **Incremental**: Make multiple Copilot calls if the change is complex
4. **Test early**: Run tests after the first build to catch issues quickly

### Testing & Validation

1. **Run focused tests first**: Don't run the full test suite if a smaller test exists
2. **Capture failures clearly**: Return detailed error output to the build step for fixes
3. **Lint before PR**: Fix all lint/format issues before opening a PR
4. **Type check**: If the repo uses mypy, ensure no type errors

### Commit Messages

Use **conventional commit format**:

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`  
Scope: Module or feature name (optional)  
Subject: Imperative, present tense  
Body: Detailed explanation (optional)  
Footer: References to issues (optional)

**Example:**
```
feat(retry): Add exponential backoff decorator

Implements @cache decorator with configurable LRU and TTL support.
Follows existing @retry decorator pattern.

Tests: 8 new test cases
Lint: Passes ruff and mypy
```

### Pull Requests

1. **Clear title**: Concise, follows conventional commits
2. **Detailed description**: What changed and why
3. **Test notes**: How to verify the changes
4. **Scope**: Small, focused PRs are preferred (one feature or fix per PR)
5. **No breaking changes**: Unless explicitly approved

**PR Template:**
```
## Description
Brief summary of changes.

## Changes
- Item 1
- Item 2

## Testing
How to verify:
- Run `pytest tests/...`
- Lint: `ruff check ...`

## Notes
Any relevant context or decisions made.
```

### Worktree Cleanup

- Builder Copilot automatically manages worktrees
- Old/abandoned worktrees should be manually cleaned up:
  ```bash
  rm -rf /worktrees/<old-work-item-id>
  ```
- Or use git's built-in cleanup:
  ```bash
  git worktree prune
  ```

### Error Handling

If a skill fails:

1. **Plan failures**: Likely due to malformed request or inaccessible repo
2. **Build failures**: Inspect Copilot output; may need to rebuild with more context
3. **Test failures**: Run tests locally to debug; return to build for fixes
4. **Lint failures**: Fix with auto-formatters or return to build for contextual fixes
5. **PR failures**: Usually due to git/GitHub auth issues; check credentials and branch status

---

## Troubleshooting

### Common Issues

**Copilot timeout**
- Increase `PROGENITOR_COPILOT_TIMEOUT_SECONDS`
- Reduce prompt size or scope

**Worktree creation fails**
- Ensure `PROGENITOR_PROJECT_ROOT` is a valid git repo
- Check disk space and permissions

**Tests fail after build**
- Inspect Copilot's diff carefully
- Return to build with specific error output
- May need to adjust test environment

**PR creation fails**
- Verify GitHub CLI (`gh`) is installed and authenticated
- Check branch permissions and remote URL

**Git identity issues**
- Set `GIT_AUTHOR_NAME` and `GIT_AUTHOR_EMAIL` in environment

---

## References

- **DeepAgent Specification**: See `prompts/skills/` for SKILL.md definitions
- **Copilot CLI Documentation**: GitHub Copilot CLI user guide
- **A2A Protocol**: `a2a-sdk` documentation
- **LangGraph**: `langgraph` documentation for multi-agent patterns

---

**Version**: 0.1.0  
**Last Updated**: 2026-03-04
