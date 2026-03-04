# Builder Copilot

## Purpose and Overview

Builder Copilot is an autonomous build agent that orchestrates the complete software development workflow for repository changes. It operates as a specialized DeepAgent integrated with GitHub Copilot, designed to handle code modifications, validation, and pull request generation with minimal human intervention.

The agent implements a deterministic, ordered workflow:

```
Request → Plan → Build → Test → Lint → PR
```

This structured approach ensures that changes are well-planned, correctly implemented, thoroughly validated, and properly integrated back into the repository.

## Key Capabilities and Features

### Worktree-Based Isolation
- Automatically creates isolated git worktrees for each task
- Prevents conflicts with the main repository during development
- Maintains clean state separation between concurrent tasks
- Automatic cleanup of worktrees post-completion

### Structured Skill-Driven Execution
- Task execution follows explicit, ordered skills
- Each skill has defined responsibilities and allowed tools
- Skills can be used independently or as part of the complete workflow
- Clear progression prevents out-of-order execution (e.g., testing before building)

### Python Code Search
- Rapid symbol and reference lookup via `python-search` skill
- Uses ripgrep for fast, pattern-based file searching
- Enables accurate code inspection before making changes
- Supports glob patterns for precise file filtering

### Automated Validation
- Integrated test execution with focused command selection
- Lint and format validation before PR creation
- Clear failure reporting for quick diagnosis and repair
- Returns to build step on test/lint failures

### Deterministic PR Workflow
- Validates all changes (build + test + lint) before committing
- Enforces clean commit messages with change summaries
- Creates pull requests with detailed descriptions
- Returns PR URLs for tracking and human review

## Available Skills

### `plan`
**Description:** Plan the requested change before editing.

**When to Use:** At the start of any new builder-copilot task to establish scope, validate the worktree, and preview the build/test/lint/PR path.

**Tools:** `summarize_request`, `create_worktree`, `python_search`, `run_worktree_command`

**Workflow:**
1. Normalize and clarify the request
2. Create or validate a worktree
3. Inspect relevant source code
4. Articulate the exact execution plan

---

### `build`
**Description:** Implement changes in a worktree using GitHub Copilot and file manipulation tools.

**When to Use:** After planning to apply code changes. Can be invoked multiple times to iteratively refine implementations.

**Tools:** `run_copilot_build`, `build_progenitor`, `run_worktree_command`, `python_search`

**Workflow:**
1. Use `run_copilot_build` to invoke GitHub Copilot with a specific prompt
2. Inspect changes via `python_search` or `run_worktree_command`
3. Repeat until implementation matches the plan
4. Proceed to test or return to plan if scope changes

---

### `test`
**Description:** Run focused validation commands for the active worktree.

**When to Use:** After building and before linting to catch functional issues early. Use before creating a PR unless explicitly skipped.

**Tools:** `run_worktree_command`, `execute`

**Workflow:**
1. Run the smallest relevant test command first
2. Capture output and analyze failures
3. If failures occur, return to build or plan
4. If all tests pass, proceed to lint

---

### `lint`
**Description:** Run lint or formatting validation for the active worktree.

**When to Use:** After testing to ensure code style and formatting compliance. Required before PR creation.

**Tools:** `run_worktree_command`, `execute`

**Workflow:**
1. Execute the repository's narrowest relevant lint command
2. Address any style or formatting issues
3. Optionally re-run tests after lint fixes
4. Proceed to PR creation once lint passes

---

### `pr`
**Description:** Commit validated work and open a pull request.

**When to Use:** Only after the build, test, and lint steps are complete and all validations pass.

**Tools:** `commit_changes`, `create_pull_request`, `run_worktree_command`

**Workflow:**
1. Verify there are uncommitted changes in the worktree
2. Create a clear, descriptive commit message
3. Commit all changes
4. Open a pull request with title, summary, and test notes
5. Return the PR URL as the task outcome

---

### `python-search`
**Description:** Search Python code quickly before changing behavior.

**When to Use:** When locating symbols, imports, test references, or understanding code structure before editing.

**Tools:** `python_search`, `grep`, `execute`

**Workflow:**
1. Prefer `python_search` for targeted symbol lookups
2. Use glob patterns for precise filtering (e.g., `tests/**/*.py`, `src/**/*.py`)
3. Review results to understand code dependencies
4. Proceed to build or ask clarifying questions in plan

---

## Configuration and Setup Instructions

### Prerequisites
- Python 3.10+
- Git with worktree support
- GitHub CLI (`gh`) for pull request operations
- GitHub Copilot CLI access (with valid token)
- Bash or PowerShell for command execution

### Repository Setup
1. Clone the main repository to the project root
2. Ensure `.git` directory exists and is properly initialized
3. Set git user identity (via environment variables or local config):
   ```bash
   git config --global user.name "Your Name"
   git config --global user.email "your.email@example.com"
   ```

### Environment Expectations
The agent reads configuration from:
- `PROGENITOR_PROJECT_ROOT`: Root directory of the target repository
- `PROGENITOR_REPO_URL`: URL of the git repository (used for cloning/initialization)
- `AZURE_OPENAI_BASE_URL`: LLM endpoint (Azure or OpenAI-compatible)
- `AZURE_OPENAI_API_KEY`: API key for LLM access
- `PROGENITOR_MODEL_DEFAULT`: Default model ID (e.g., `gpt-5.2-chat`)
- `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL`: Git identity for commits

### Worktree Management
- Worktrees are created in `PROGENITOR_WORKTREE_DIR` (typically `.progenitor_worktree/`)
- Each worktree is named after a unique work item ID
- Worktrees are isolated from the main repository, preventing build conflicts
- Cleanup is managed externally; old worktrees should be periodically pruned

## How to Invoke the Agent via A2A Protocol

Builder Copilot is invoked through the A2A (Agent-to-Agent) protocol, typically by the main Progenitor agent or external systems.

### Example JSON Request

```json
{
  "request_id": "builder-task-12345",
  "agent_name": "builder-copilot",
  "task_type": "build_and_pr",
  "payload": {
    "request": "Add a new feature to handle batch processing",
    "context": {
      "target_area": "app/progenitor/tools",
      "pr_base_branch": "main",
      "skip_test": false,
      "skip_lint": false
    }
  }
}
```

### Required Fields
- `request`: The user's or system's description of the change needed
- `context`: Dictionary containing:
  - `target_area` (optional): Specific directory or module for the change
  - `pr_base_branch` (optional): Base branch for the PR (default: `main`)
  - `skip_test` (optional): Set to `true` to skip testing (not recommended)
  - `skip_lint` (optional): Set to `true` to skip linting (not recommended)

### Response Format
```json
{
  "status": "success",
  "worktree_path": "/path/to/.progenitor_worktree/builder-task-12345",
  "pr_url": "https://github.com/owner/repo/pull/123",
  "summary": "Feature added: batch processing support with full test coverage"
}
```

### Lifecycle of a Request

1. **Reception**: A2A request arrives with a user/system prompt and optional context
2. **Planning**: Agent creates a preflight plan via LLM (structured planning response)
3. **Worktree Creation**: Isolated git worktree is created from the main branch
4. **Build**: GitHub Copilot makes targeted code changes based on the plan
5. **Test**: Relevant test suite is executed; failures trigger a return to build
6. **Lint**: Code formatting and linting are validated; issues are resolved
7. **PR**: Changes are committed and a pull request is opened
8. **Completion**: PR URL and summary are returned to the caller

## Usage Examples

### Example 1: Adding a Feature

**User Request:**
> "Add a health check endpoint to the FastAPI backend that returns service status."

**Agent Execution Flow:**

1. **Plan**
   - Normalizes request to "Add GET /health endpoint to FastAPI backend"
   - Creates worktree `builder-20250304-a1b2c3`
   - Searches for existing endpoint patterns in `app/backend/main.py`
   - Outlines: inspect endpoints → add health check → write test → validate

2. **Build**
   - Invokes Copilot with prompt: "Add a GET /health endpoint returning {status: 'ok', uptime: seconds}"
   - Modifies `app/backend/main.py` and `app/backend/routes/health.py` (new file)
   - Verifies changes with `git diff`

3. **Test**
   - Runs `pytest tests/backend/test_health.py -v`
   - Verifies endpoint responds with correct JSON structure
   - Checks status codes for success scenarios

4. **Lint**
   - Runs `ruff check app/backend` and `black --check app/backend`
   - Applies formatting fixes if needed
   - Re-runs tests to ensure stability

5. **PR**
   - Commits with message: "feat: add health check endpoint"
   - Creates PR: "Add health check endpoint to FastAPI backend"
   - Returns PR URL for review

---

### Example 2: Refactoring Code

**User Request:**
> "Refactor the state manager to use async/await patterns instead of callbacks."

**Agent Execution Flow:**

1. **Plan**
   - Locates state manager in `app/progenitor/state/manager.py`
   - Uses `python-search` to find all callback usages
   - Creates plan: identify callbacks → convert to async → update callers → test integration

2. **Build**
   - Copilot systematically refactors callbacks to async functions
   - Updates all callers (multiple files)
   - Adds `await` keywords where needed

3. **Test**
   - Runs `pytest tests/progenitor/test_state.py -v`
   - Runs integration tests for the full loop
   - Verifies no functionality degradation

4. **Lint**
   - Checks for type consistency with `mypy app/progenitor`
   - Applies formatting standards

5. **PR**
   - Opens PR with detailed description of refactoring scope
   - Links related issues if applicable

---

### Example 3: Updating Documentation

**User Request:**
> "Update README.md to include examples of the new python-search skill."

**Agent Execution Flow:**

1. **Plan**
   - Views current README structure
   - Identifies documentation sections
   - Plans: add skill description → add usage examples → validate formatting

2. **Build**
   - Copilot edits README.md with new sections
   - Adds code examples for `python-search`
   - Maintains consistency with existing format

3. **Test**
   - Verifies README renders correctly in markdown
   - Checks for broken links or references (if applicable)

4. **Lint**
   - Checks markdown formatting
   - Validates links

5. **PR**
   - Opens documentation PR
   - Includes change summary

---

## Architecture and Implementation Details

### High-Level Architecture

```
A2A Request
    ↓
BuilderCopilotService
    ├─ Initialization (LLM config, workspace)
    ├─ Preflight Planning (structured LLM response)
    └─ DeepAgent Execution
         ├─ Langchain Model (with middleware)
         ├─ LocalShellBackend (command execution)
         ├─ Skills (plan, build, test, lint, pr, python-search)
         ├─ Tools (worktree, Copilot, git, shell)
         └─ Middleware (secret redaction, audit logging, metrics)
            ↓
         Result String (worktree path, PR URL, summary)
```

### Skill System

Skills are defined in `app/agents/builder_copilot/skills/` with:
- `SKILL.md`: Metadata (name, description, allowed tools)
- Embedded prompts guiding LLM behavior within the skill
- Allowed tools narrowly scoped to prevent misuse

The agent selects the appropriate skill based on the current task phase and user guidance.

### Worktree Management

The `GitWorktreeManager` (from `builder_agent.tools.git`) handles:
- Creating isolated worktrees from the main branch
- Managing worktree lifecycle (creation, tracking, cleanup)
- Providing clean, independent build environments
- Preventing interference with the main repository

### Tooling Integration

#### Core Tools
- **`create_worktree`**: Create an isolated git worktree
- **`run_copilot_build`**: Invoke GitHub Copilot with a prompt and target directory
- **`build_progenitor`**: Specialized wrapper for `app/progenitor` modifications
- **`run_worktree_command`**: Execute shell commands within the worktree
- **`python_search`**: Fast Python symbol/reference search using ripgrep
- **`commit_changes`**: Stage and commit modifications
- **`create_pull_request`**: Open a PR with title, description, and branch

#### Middleware
- **SecretRedactionMiddleware**: Masks API keys, tokens, and credentials in logs
- **AuditLoggingMiddleware**: Writes all tool calls to JSONL audit logs
- **MetricsMiddleware**: Collects execution time and call counts

### Execution Lifecycle

1. **Request Reception**: A2A handler receives JSON request
2. **Configuration Loading**: Environment and config files are loaded
3. **Workspace Initialization**: Ensures `.git` exists, clones if needed
4. **Preflight Planning**: Optional LLM-based planning (structured response)
5. **DeepAgent Invocation**: LangGraph graph runs the agent loop
6. **Skill Execution**: Agent selects and executes appropriate skills
7. **Tool Invocation**: Skills call tools (worktree, Copilot, shell)
8. **Result Extraction**: Final output is parsed and returned
9. **Status Reporting**: A2A response is sent with status, URL, and summary

---

## Key File Paths to Important Modules

### Agent Core
- **`app/agents/builder_copilot/agent.py`** - A2A entry point, request handler, skill definitions
- **`app/agents/builder_copilot/deep_agent.py`** - DeepAgent factory, LLM configuration, system prompt
- **`app/agents/builder_copilot/tools.py`** - Tool definitions (worktree, Copilot, shell, git)
- **`app/agents/builder_copilot/client.py`** - Azure Responses API client for planning
- **`app/agents/builder_copilot/config.py`** - Configuration dataclasses and loading logic

### Skills
- **`app/agents/builder_copilot/skills/plan/SKILL.md`** - Planning skill definition
- **`app/agents/builder_copilot/skills/test/SKILL.md`** - Testing skill definition
- **`app/agents/builder_copilot/skills/lint/SKILL.md`** - Linting skill definition
- **`app/agents/builder_copilot/skills/pr/SKILL.md`** - Pull request skill definition
- **`app/agents/builder_copilot/skills/python-search/SKILL.md`** - Code search skill definition

### Middleware & Integration
- **`app/agents/builder_copilot/middleware.py`** - Redaction, audit logging, metrics
- **`app/agents/builder_copilot/langchain_adapter.py`** - LLM model creation and configuration

### Dependencies (Builder Agent Utilities)
- **`app/agents/builder_agent/tools/git.py`** - GitWorktreeManager
- **`app/agents/builder_agent/tools/copilot.py`** - CopilotCLI (GitHub Copilot invocation)
- **`app/agents/builder_agent/tools/gh.py`** - GitHub PR and issue tools

---

## Integration with Progenitor

Builder Copilot operates as a specialized sub-agent within the Progenitor system. The main Progenitor agent routes build-related A2A requests to Builder Copilot, receives execution results, and incorporates them into its overall workflow.

For questions or issues, refer to the main [AGENTS.md](AGENTS.md) documentation or the Progenitor architecture documentation.
