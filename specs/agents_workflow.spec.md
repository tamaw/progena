# GitHub Actions Workflow Specification — Agents Pipeline

> Describes every job and step in the agent CI/CD workflows so the pipeline can
> be reconstructed without reading the raw YAML. Two workflows are covered:
>
> - **`agents.yml`** — Tests, builds, and deploys agents and MCP servers.
> - **`ci.yml`** — Tests, builds, and deploys the core Progenitor stack
>   (backend, progenitor loop, frontend). Included for context since agents.yml
>   depends on the same self-hosted runner infrastructure.

---

## Workflow: `agents.yml`

**File:** `.github/workflows/agents.yml`  
**Name:** `agents`

### Trigger Conditions

| Trigger | Condition |
|---|---|
| `workflow_dispatch` | Manual run from the GitHub UI. Accepts a `mode` input: `changed` (default) or `all`. |
| `push` (auto) | Any push that modifies files under `app/agents/**`, `app/mcp/**`, `docker/runner/**`, or `.github/workflows/agents.yml`. |

When triggered by `push`, the `mode` defaults to `changed`, meaning only agents whose source files changed since the previous commit are built and deployed.  
When triggered manually with `mode=all`, every discovered agent is built and deployed regardless of file changes.

### Concurrency Policy

- Runs are grouped by `workflow + git ref`.
- On **non-main branches**, any in-progress run is cancelled when a new one starts (fast feedback on PRs).
- On **main branch**, runs are queued (not cancelled) so consecutive merges deploy sequentially.

### Permissions

| Permission | Level | Reason |
|---|---|---|
| `contents` | `read` | Check out source code |
| `packages` | `write` | Push Docker images to `ghcr.io` |

---

### Job: `runner-context`

**Purpose:** Capture the numeric UID and GID of the self-hosted runner user so that Docker containers started later can run as the same user, avoiding permission mismatches on bind-mounted volumes.

**Runs on:** `self-hosted`  
**Outputs:** `uid`, `gid`

#### Steps

1. **Capture runner uid/gid**
   - Runs `id -u` and `id -g` to get the current user's numeric user ID and group ID.
   - Writes both values to `$GITHUB_OUTPUT` as `uid=<N>` and `gid=<N>`.
   - These are passed to downstream jobs via `needs.runner-context.outputs.uid/gid`.

---

### Job: `discover-agents`

**Purpose:** Dynamically enumerate every agent directory under `app/agents/` so the test matrix is always up-to-date without manual YAML edits when new agents are added.

**Runs on:** `self-hosted`  
**Needs:** `runner-context`  
**Outputs:** `agents` — a JSON array of agent name strings, e.g. `["builder_agent","builder_progenitor","observe_agent"]`

#### Steps

1. **Checkout** (`actions/checkout@v4`)
   - Clones the repository at the current commit.

2. **Discover agents**
   - Uses `find app/agents -mindepth 1 -maxdepth 1 -type d` to list all immediate subdirectories of `app/agents/`.
   - Excludes `__pycache__` and any hidden directories (names starting with `.`).
   - Sorts the names alphabetically with `sort`.
   - Converts each name to a JSON string with `jq -R .` and collects them into a JSON array with `jq -sc .`.
   - Writes the result to `$GITHUB_OUTPUT` as `agents=<json-array>`.
   - Prints the list to the log for visibility.

---

### Job: `test-agent`

**Purpose:** Run code quality checks (lint, type-check) and tests for every discovered agent in parallel using a matrix strategy.

**Runs on:** `self-hosted, Linux`  
**Container:** `python:3.12-slim`, running as the runner's `uid:gid`  
**Needs:** `runner-context`, `discover-agents`  
**Strategy:** Matrix over `discover-agents.outputs.agents`; `fail-fast: false` so all agents are tested even if one fails.

**Environment variables set for every step:**

| Variable | Value | Purpose |
|---|---|---|
| `AGENT_NAME` | `matrix.agent` | Identifies which agent is being tested |
| `PIP_CACHE_DIR` | `/tmp/pip-cache` | Shared pip download cache |
| `PROGENITOR_LOG_DIR` | `/app/logs` | Matches production log path |
| `PROGENITOR_STATE_DIR` | `/app/data/state` | Matches production state path |

#### Steps

1. **Checkout** (`actions/checkout@v4`)
   - Fetches the full working tree.

2. **Install Python deps**
   - Creates `/tmp/pip-cache` if it doesn't exist (pip cache for speed).
   - Creates a virtual environment at `.venv`.
   - Activates the venv with `. .venv/bin/activate`.
   - Upgrades `pip` to the latest version.
   - Installs the pinned root dependencies from `requirements.lock.txt`.
   - If `app/agents/<agent>/requirements.txt` exists, installs the agent's extra dependencies.
   - Installs `ruff` (linter) and `pyright` (type checker).
   - Installs the project itself in no-deps editable mode (`pip install -e . --no-deps`) so imports from `app/` resolve correctly.

3. **Setup Node.js** (`actions/setup-node@v4`, Node 20)
   - Installs Node.js 20 into the container.
   - Required because some test utilities or DeepAgent tooling may depend on Node at runtime.

4. **Lint (ruff)**
   - Activates the venv.
   - Sets `TARGETS` to `app/agents/<agent>`.
   - If `tests/agents/<agent>/` exists, appends it to `TARGETS`.
   - Runs `ruff check $TARGETS` — any lint error fails the step.

5. **Type-check (pyright)**
   - Activates the venv.
   - Runs `pyright app/agents/<agent>` — any type error fails the step.

6. **Tests (pytest)**
   - Activates the venv.
   - Checks whether `tests/agents/<agent>/` exists.
   - If yes, runs `pytest <test-dir> -q` (quiet output).
   - If no tests directory exists, prints `"No tests found for <agent>, skipping."` and exits successfully.

---

### Job: `detect-changes`

**Purpose:** Determine which agents and MCP servers have changed since the last commit (or enumerate all if `mode=all`), so only affected components are built and deployed.

**Condition:** Only runs on pushes to `main` or when triggered by `workflow_dispatch`. Skipped on PR branches to prevent accidental deploys.

**Runs on:** `self-hosted, Linux`  
**Needs:** `runner-context`  
**Outputs:** `agents`, `mcps` — JSON arrays of component names that need building.

#### Steps

1. **Checkout** (`actions/checkout@v4`, `fetch-depth: 0`)
   - Full clone with complete git history, required to compute diffs between commits.

2. **Detect changed agents and MCP servers**
   - Sets env vars: `EVENT_NAME` (from `github.event_name`) and `MODE` (from `inputs.mode`).
   - Runs the Python script `.github/scripts/detect_changed_components.py`.
   - This script inspects git diff `HEAD^..HEAD` (or all directories if `mode=all` or first commit) and writes the `agents` and `mcps` JSON arrays to `$GITHUB_OUTPUT`.

---

### Job: `build-agent`

**Purpose:** Build and push a Docker image for each changed agent to the GitHub Container Registry (`ghcr.io`).

**Condition:** Only runs if `detect-changes.outputs.agents` is non-empty.  
**Needs:** `test-agent`, `detect-changes`  
**Runs on:** `self-hosted`  
**Strategy:** Matrix over `detect-changes.outputs.agents`; `fail-fast: false`.

#### Steps

1. **Checkout** (`actions/checkout@v4`)
   - Fetches the source tree including the agent's Dockerfile.

2. **Setup Docker Buildx** (`docker/setup-buildx-action@v3`)
   - Enables BuildKit and multi-platform build support.
   - Required for build cache (`type=gha`) to function.

3. **Docker login to GHCR** (`docker/login-action@v3`)
   - Authenticates with `ghcr.io` using `github.actor` and `secrets.GITHUB_TOKEN` (auto-provisioned per run).

4. **Detect agent Dockerfile**
   - Checks if `app/agents/<agent>/Dockerfile` exists.
   - If yes, uses the agent's own custom Dockerfile.
   - If no, falls back to the generic runner Dockerfile at `docker/runner/Dockerfile.agent`.
   - Writes the resolved path to `$GITHUB_OUTPUT` as `file=<path>`.

5. **Build and push agent image** (`docker/build-push-action@v6`)
   - Build context: repository root (`.`).
   - Dockerfile: resolved in the previous step.
   - Build arg: `AGENT_NAME=<agent>` passed into the image (used by the generic Dockerfile to install the correct agent package).
   - Push: `true` — the image is always pushed on this job.
   - Labels applied:
     - `com.progenitor.managed=true` — used by image cleanup filters.
     - `com.progenitor.component=agent` — identifies the component type.
   - Tags pushed:
     - `ghcr.io/tamaw/progenitor-agent-<agent>:latest`
     - `ghcr.io/tamaw/progenitor-agent-<agent>:<git-sha>`
   - Cache: GitHub Actions cache (`type=gha`, `mode=max`) — layers are cached between runs to speed up builds.

---

### Job: `deploy-agent`

**Purpose:** Pull the freshly built image, stop any existing container, start a new container with the correct environment and volumes, verify health, and register the agent with the Progenitor registry.

**Condition:** Only runs if `detect-changes.outputs.agents` is non-empty.  
**Needs:** `detect-changes`, `build-agent`  
**Runs on:** `self-hosted`  
**Environment:** `cronix` — GitHub Environment with secrets and approval gates.  
**Strategy:** Matrix over `detect-changes.outputs.agents`; `fail-fast: false`.

#### Steps

1. **Checkout** (`actions/checkout@v4`)
   - Needed to run the deploy helper Python scripts.

2. **Setup Python 3.12** (`actions/setup-python@v5`)
   - Installs Python 3.12 for running the deploy scripts.

3. **Validate agent directory exists**
   - Checks that `app/agents/<agent>/` exists; fails with a clear message if not.
   - Checks that `app/agents/<agent>/Dockerfile` exists; fails with a clear message if not.
   - These checks prevent deploying a ghost agent if the matrix was derived from a stale cache.

4. **Resolve agent runtime config**
   - Injects all secrets and variables as environment variables: `AGENT_NAME`, `COPILOT_GITHUB_TOKEN`, `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_API_KEY`, `GH_TOKEN`, `AGENT_MODEL_DEFAULT`, model overrides, `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `PROGENITOR_REPO_URL`.
   - Runs `.github/scripts/get_agent_runtime_config.py`.
   - This script reads the agent directory and its metadata, then writes several values to `$GITHUB_OUTPUT`:
     - `port` — the port number the agent will listen on.
     - `volume_args` — the `--volume` flags for `docker run`.
     - `chown_paths` — host paths that must be pre-created and owned by the runner user.
     - `env_args` — additional `--env` flags for agent-specific config.
     - `tmpfs_args` — any `--tmpfs` flags for ephemeral in-memory storage.

5. **Docker login (GHCR)**
   - Authenticates `docker` CLI with `ghcr.io` using `secrets.GITHUB_TOKEN`.

6. **Pull agent image**
   - Runs `docker pull ghcr.io/tamaw/progenitor-agent-<agent>:latest`.
   - Ensures the latest freshly built image is present on the runner before stopping the old container.

7. **Stop existing container (if any)**
   - Runs `docker rm -f progenitor-agent-<agent>` to force-remove any existing container.
   - The `2>/dev/null || true` guard makes the step succeed even if no container exists.

8. **Run agent container**
   - Pre-creates any required host paths from `chown_paths` (using `sudo mkdir -p` where available).
   - Sets ownership of those paths to the runner's `uid:gid` (using `sudo chown -R` where available).
   - Sets execute permissions with `chmod -R u+rwX`.
   - Unconditionally ensures three canonical runtime directories exist and are correctly owned:
     - `/var/lib/progenitor/data`
     - `/var/lib/progenitor/logs`
     - `/var/lib/progenitor/worktrees`
   - If `GH_TOKEN` is set, rewrites `PROGENITOR_REPO_URL` to include the token for authenticated git cloning.
   - Runs `docker run -d` with:
     - `--name progenitor-agent-<agent>` — deterministic container name.
     - `--restart unless-stopped` — auto-restart on crash or host reboot.
     - `--network progenitor_network` — joins the shared Docker network so agents can call each other and the backend.
     - Standard env vars: `AGENT_NAME`, `A2A_PUBLIC_HOST`, `PROGENITOR_STATE_DIR`, `PROGENITOR_LOG_DIR`, `PROGENITOR_PROJECT_ROOT`, `PROGENITOR_WORKTREE_DIR`, `PROGENITOR_WORKTREES_ROOT`, `GH_TOKEN`, `COPILOT_GITHUB_TOKEN`.
     - Dynamic args: `VOLUME_ARGS`, `TMPFS_ARGS`, `ENV_ARGS` (from the runtime config step).
     - Image: `ghcr.io/tamaw/progenitor-agent-<agent>:latest`.

9. **Wait for agent to become healthy**
   - Resolves the container hostname using `docker inspect --format '{{.Config.Hostname}}'`.
   - Polls the agent's `/health` HTTP endpoint up to 30 times with a 2-second sleep between attempts.
   - Health check is performed via `docker exec` into the container, using `httpx` to call `http://<hostname>:<port>/health`.
   - If the health check passes, the step exits with code 0.
   - If all 30 attempts fail, the step tails the last 50 log lines from the container and exits with code 1.
   - Outputs `docker_hostname` and `registry_host` for use by the registration step.

10. **Register agent in Progenitor registry** (only if health check passed)
    - Injects `AGENT_NAME`, `PORT`, `DOCKER_HOSTNAME`, `PROGENITOR_CONTAINER` env vars.
    - Runs `.github/scripts/register_agent.py`.
    - This script sends an HTTP request to the running Progenitor container to register the new agent's A2A endpoint in its internal registry, making the agent callable from Progenitor.

11. **Cleanup unused managed images** (only if previous steps succeeded)
    - Runs `docker image prune -af` with filters:
      - `label=com.progenitor.managed=true` — only prune images tagged by this pipeline.
      - `until=168h` — only prune images older than 7 days.
    - Frees disk space on the runner without risking removal of images still in use or from other systems.

---

### Job: `build-mcp`

**Purpose:** Build and push Docker images for any changed MCP (Model Context Protocol) servers.

**Condition:** Only runs if `detect-changes.outputs.mcps` is non-empty.  
**Needs:** `detect-changes`  
**Runs on:** `self-hosted`  
**Strategy:** Matrix over `detect-changes.outputs.mcps`; `fail-fast: false`.

#### Steps

1. **Checkout** (`actions/checkout@v4`)

2. **Setup Docker Buildx** (`docker/setup-buildx-action@v3`)
   - Same as build-agent.

3. **Docker login to GHCR** (`docker/login-action@v3`)
   - Same as build-agent.

4. **Build and push MCP image** (`docker/build-push-action@v6`)
   - Build context: repository root.
   - Dockerfile: `docker/runner/Dockerfile.mcp` (single shared MCP Dockerfile).
   - Build arg: `MCP_NAME=<mcp>`.
   - Labels:
     - `com.progenitor.managed=true`
     - `com.progenitor.component=mcp`
   - Tags:
     - `ghcr.io/tamaw/progenitor-mcp-<mcp>:latest`
     - `ghcr.io/tamaw/progenitor-mcp-<mcp>:<git-sha>`
   - Cache: GitHub Actions cache.

---

### Job: `deploy-mcp`

**Purpose:** Pull, stop, start, and health-check each changed MCP server container.

**Condition:** Only runs if `detect-changes.outputs.mcps` is non-empty.  
**Needs:** `detect-changes`, `build-mcp`  
**Runs on:** `self-hosted`  
**Environment:** `cronix`  
**Strategy:** Matrix over `detect-changes.outputs.mcps`; `fail-fast: false`.

#### Steps

1. **Checkout** (`actions/checkout@v4`)

2. **Validate MCP directory exists**
   - Checks that `app/mcp/<mcp>/` exists; fails with a clear message if not.

3. **Docker login (GHCR)**
   - Same pattern as deploy-agent.

4. **Pull MCP image**
   - Runs `docker pull ghcr.io/tamaw/progenitor-mcp-<mcp>:latest`.

5. **Stop existing container (if any)**
   - Runs `docker rm -f progenitor-mcp-<mcp>` with `2>/dev/null || true` guard.

6. **Run MCP container**
   - Runs `docker run -d` with:
     - `--name progenitor-mcp-<mcp>`
     - `--restart unless-stopped`
     - `--network progenitor_network`
     - `-e TEST_MODE=false` — ensures the server runs in production mode.
   - MCP servers have a simpler runtime configuration than agents (no volume mounts, no git credentials).

7. **Wait for MCP server to become healthy**
   - Same polling pattern as deploy-agent.
   - Checks `http://localhost:8080/health` inside the container.
   - Polls up to 30 times with 2-second sleeps.
   - On failure, tails 50 log lines and exits with code 1.

8. **Cleanup unused managed images** (only if previous steps succeeded)
   - Same image prune as deploy-agent.

---

## Job Dependency Graph (agents.yml)

```
runner-context
     │
     ├── discover-agents
     │        │
     │        └── test-agent (matrix: all agents)
     │                 │
     │                 └── build-agent (matrix: changed agents)
     │                          │
     │                          └── deploy-agent (matrix: changed agents)
     │
     └── detect-changes (main/dispatch only)
              │
              ├── build-agent ─── deploy-agent
              │
              └── build-mcp ─── deploy-mcp
```

---

## Workflow: `ci.yml`

**File:** `.github/workflows/ci.yml`  
**Name:** `ci`

### Trigger Conditions

| Trigger | Condition |
|---|---|
| `workflow_dispatch` | Manual run. |
| `push` (auto) | Any push that does **not** touch `app/agents/**`, `app/mcp/**`, or `.github/workflows/agents.yml`. |

The `push` filter is the inverse of `agents.yml`, so changes to the core stack and changes to agents trigger the correct workflow independently.

### Concurrency Policy

Same as `agents.yml`: cancel in-progress on non-main branches; queue on main.

---

### Job: `runner-context`

Identical to `agents.yml` — captures runner `uid:gid` for container permission alignment.

---

### Job: `backend`

**Purpose:** Lint and test the FastAPI backend (`app/backend/`).

**Runs on:** `self-hosted, Linux`  
**Container:** `python:3.12-slim` running as runner's `uid:gid`  
**Needs:** `runner-context`

#### Steps

1. **Checkout**

2. **Install Python deps**
   - Same venv + pip workflow as test-agent.
   - Installs from `requirements.lock.txt` + `ruff`.

3. **Lint backend (ruff)** (`continue-on-error: true`)
   - Runs `ruff check app/backend tests/backend`.
   - `continue-on-error: true` means lint failures are reported but do not block the test step.

4. **Tests backend (pytest)**
   - Runs `pytest tests/backend` — failures block the job.

---

### Job: `progenitor`

**Purpose:** Lint, type-check, and test the core Progenitor loop (`app/progenitor/`).

**Runs on:** `self-hosted, Linux`  
**Container:** `python:3.12-slim`  
**Needs:** `runner-context`

#### Steps

1. **Checkout**

2. **Install Python deps**
   - Same as backend, plus installs `pyright`.

3. **Setup Node.js** (`actions/setup-node@v4`, Node 20)
   - Required for DeepAgent tooling or test utilities that invoke Node.

4. **Lint progenitor (ruff)**
   - Runs `ruff check app/progenitor tests/progenitor tests/test_scheduler.py`.

5. **Type-check progenitor (pyright)**
   - Runs `pyright app/progenitor`.

6. **Tests progenitor (pytest)**
   - Runs pytest against:
     - `tests/progenitor/`
     - `tests/test_scheduler.py`
   - Explicitly ignores three heavy integration/E2E test files that require live infrastructure:
     - `tests/progenitor/test_dual_loop_full_integration.py`
     - `tests/progenitor/test_dual_loop_integration.py`
     - `tests/progenitor/test_e2e_dual_loop.py`

---

### Job: `frontend`

**Purpose:** Lint and build the React/Vite frontend (`app/frontend/`).

**Runs on:** `self-hosted` (no container — uses host Node.js)  
**Needs:** `runner-context`  
**Working directory:** `app/frontend` (all steps run here by default)

#### Steps

1. **Checkout**

2. **Setup Node.js** (`actions/setup-node@v4`, Node 20, npm cache)
   - Cache key derived from `app/frontend/package-lock.json` for fast installs.

3. **Install deps**
   - Runs `npm ci` — clean install from lockfile.

4. **Lint** (`continue-on-error: true`)
   - Runs `npm run lint` (ESLint).
   - `continue-on-error: true` allows lint warnings without blocking the build.

5. **Build**
   - Runs `npm run build` (Vite production build).
   - A failing build blocks the job.

---

### Job: `build-and-push`

**Purpose:** Build and push Docker images for the three core services when code merges to main.

**Condition:** Only runs on `push` or `workflow_dispatch` on the `main` branch.  
**Needs:** `backend`, `progenitor`, `frontend`  
**Runs on:** `self-hosted`

#### Steps

1. **Checkout**

2. **Setup Docker Buildx**

3. **Docker login to GHCR**
   - Same as agents workflow.

4. **Build and push progenitor image** (`docker/build-push-action@v6`)
   - Dockerfile: `docker/progenitor/Dockerfile`
   - Labels: `com.progenitor.managed=true`, `com.progenitor.component=progenitor`
   - Tags: `ghcr.io/tamaw/progenitor:latest`, `ghcr.io/tamaw/progenitor:<git-sha>`
   - Cache: GitHub Actions cache.

5. **Build and push backend image** (`docker/build-push-action@v6`)
   - Dockerfile: `docker/backend/Dockerfile`
   - Tags: `ghcr.io/tamaw/progenitor-backend:latest`, `ghcr.io/tamaw/progenitor-backend:<git-sha>`

6. **Build and push frontend image** (`docker/build-push-action@v6`)
   - Build context: `app/frontend` (not repo root, because frontend has its own build context).
   - Dockerfile: `docker/frontend/Dockerfile`
   - Tags: `ghcr.io/tamaw/progenitor-frontend:latest`, `ghcr.io/tamaw/progenitor-frontend:<git-sha>`

---

### Job: `deploy`

**Purpose:** Deploy the updated core stack to the `cronix` self-hosted server using Docker Compose.

**Condition:** Only runs on `push` or `workflow_dispatch` on `main`.  
**Needs:** `build-and-push`  
**Runs on:** `self-hosted`  
**Environment:** `cronix`

#### Steps

1. **Checkout**

2. **Ensure external Docker network**
   - Checks if `progenitor_network` exists with `docker network inspect`.
   - Creates it with `docker network create` if it does not.

3. **Deploy progenitor stack**
   - Injects all runtime secrets and variables as env vars:
     - `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_BASE_URL`
     - `PROGENITOR_MODEL_DEFAULT`, `PROGENITOR_TIMEZONE`
     - `GH_TOKEN` (from `secrets.COPILOT_GH_TOKEN`)
     - `PROGENITOR_REASONING_ENABLED`, `PROGENITOR_REASONING_BUDGET_TOKENS`
     - Telegram vars: `TELEGRAM_ENABLED`, `TELEGRAM_POLLING_ENABLED`, `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
     - Host paths: `PROGENITOR_HOST_DATA_DIR`, `PROGENITOR_HOST_LOGS_DIR`, `PROGENITOR_HOST_WORKTREES_DIR`, `PROGENITOR_HOST_TMP_DIR`, `PROGENITOR_HOST_PROMPTS_DIR`
   - Force-removes any manually started containers with the same names to avoid Compose conflicts: `docker rm -f progenitor progenitor-backend progenitor-frontend`.
   - Creates host directories for data, logs, worktrees, tmp, and prompts (with `sudo` if available).
   - Sets ownership of `/var/lib/progenitor` to runner's `uid:gid`.
   - Refreshes the prompts bind mount by deleting and re-copying `prompts/` to `PROGENITOR_HOST_PROMPTS_DIR` (ensures container sees the latest prompts without rebuilding the image).
   - Logs into GHCR.
   - Pulls all images via `docker compose pull`.
   - Starts all services via `docker compose up -d`.
   - Prints service status with `docker compose ps`.

4. **Liveness checks**
   - Defines a `wait_healthy` shell function that polls `docker inspect` for the container's health status.
   - Waits for `progenitor-backend` to become healthy (up to 90 × 2s = 3 minutes).
   - Waits for `progenitor` to become healthy (up to 120 × 2s = 4 minutes).
   - Waits for `progenitor-frontend` to become healthy (up to 60 × 2s = 2 minutes).
   - On timeout, prints all running containers and the last 200 log lines before failing.

5. **Cleanup unused managed images** (only if previous steps succeeded)
   - Same image prune policy as agents workflow.

---

## Job Dependency Graph (ci.yml)

```
runner-context
     │
     ├── backend ──────────────┐
     │                         │
     ├── progenitor ───────────┼── build-and-push ── deploy
     │                         │
     └── frontend ─────────────┘
```

---

## Helper Scripts

These Python scripts are invoked by the workflows and are located in `.github/scripts/`:

| Script | Called by | Purpose |
|---|---|---|
| `detect_changed_components.py` | `detect-changes` job | Diffs git history to find which agents/MCPs changed; supports `mode=all` override |
| `get_agent_runtime_config.py` | `deploy-agent` job | Reads agent metadata and writes `port`, `volume_args`, `env_args`, `chown_paths`, `tmpfs_args` to `$GITHUB_OUTPUT` |
| `register_agent.py` | `deploy-agent` job | Sends an HTTP POST to the running Progenitor container to register the new agent's A2A endpoint |

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Self-hosted runner for all jobs | Agents deploy to a private server (`192.168.0.2`) not reachable from GitHub-hosted runners. |
| `fail-fast: false` on all matrices | Ensures all agents are tested/deployed even when one fails, preventing a single broken agent from blocking others. |
| `uid:gid` passthrough | Containers run as the runner user to avoid root-owned bind-mount files that break future agent writes. |
| Separate `agents.yml` from `ci.yml` | Changes to agents and MCPs do not trigger a full core stack redeploy, and vice versa, reducing unnecessary rebuilds. |
| Image labels `com.progenitor.managed=true` | Allows targeted image cleanup without accidentally pruning unrelated Docker images on the runner host. |
| Two image tags (`:latest` + `:<sha>`) | `:latest` enables simple `docker pull` in deploy; `:<sha>` enables rollback to any previous commit. |
| `detect-changes` only on main/dispatch | PRs run tests only; builds and deploys are gated to main merges to prevent accidental production deploys from feature branches. |
| `cronix` GitHub Environment | Provides an approval gate and scoped secrets so that deploys cannot be triggered by unauthorized pushes. |
| Health check via `docker exec` | Avoids needing to expose agent ports on the host; the health check runs inside the container's network namespace. |
| Agent registration via `register_agent.py` | Automatically updates Progenitor's internal A2A registry so newly deployed agents are immediately callable without manual configuration. |
