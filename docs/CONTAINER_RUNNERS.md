# Containerized A2A + MCP Runners

Progenitor can orchestrate A2A agents and MCP servers while executing spawned runtimes in short-lived containers.

## Images

- `progenitor-agent-runner:latest`
- `progenitor-mcp-runner:latest`

Build locally:

```bash
docker build -f docker/runner/Dockerfile.agent -t progenitor-agent-runner:latest .
docker build -f docker/runner/Dockerfile.mcp -t progenitor-mcp-runner:latest .
```

## Local PyPI cache for self-hosted builds

Self-hosted GitHub Actions workflows can bootstrap a persistent local PyPI cache
container with `.github/scripts/ensure_local_pypi_cache.py`. That script starts
or reuses a `devpi` container on the runner host, then emits `PIP_INDEX_URL`
and `PIP_TRUSTED_HOST` values for both workflow `pip install` steps and Docker
image builds.

Manual Docker builds can opt into the same cache with build args:

```bash
docker build \
  -f docker/runner/Dockerfile.agent \
  --build-arg PIP_INDEX_URL=http://<runner-host>:3141/root/pypi/+simple/ \
  --build-arg PIP_TRUSTED_HOST=<runner-host> \
  --build-arg AGENT_NAME=<agent_name> \
  -t progenitor-agent-runner:latest .
```

The workflow bootstrap step also supports these overrides via GitHub Actions
variables or secrets:

- `PYPI_CACHE_IMAGE`
- `PYPI_CACHE_BIND_ADDRESS`
- `PYPI_CACHE_HOST`
- `PYPI_CACHE_PORT`
- `PYPI_CACHE_CONTAINER_NAME`
- `PYPI_CACHE_VOLUME_NAME`
- `PYPI_CACHE_ROOT_PASSWORD` (secret)

## Runner Contract

Container entrypoint:

```bash
python -m app.progenitor.runtime.container_runner --mode=a2a|mcp --config=/workspace/run.json
```

`run.json` fields:

- `name`: logical run name
- `mode`: `a2a` or `mcp`
- `transport`: `stdio` or `http`
- `command`: executable + args prefix
- `args`: additional args
- `timeout_seconds`: readiness or request timeout
- `env_allowlist`: env var names allowed from host
- `env`: explicit per-run env overrides
- `http.ports`: list of `{host, container}` mappings (for HTTP mode)
- `http.readiness_path`: endpoint path used for readiness probes

## Per-run Workspace Layout

Each run gets an isolated directory under:

- `data/state/container_runs/<run-name>-<run-id>/workspace/`

Contents:

- `run.json` (contract bundle)
- any runtime artifacts produced by the agent/tool

Only this workspace is mounted by default (`/workspace:rw`).

## Progenitor Spawner

`ContainerSpawner` lives in `app/progenitor/runtime/container_spawner.py` and exposes:

- `spawn_agent(config) -> ProcessHandle`
- `spawn_mcp(config) -> ProcessHandle`
- `stop(handle)`

The spawner uses:

- `--rm`
- explicit mounts
- explicit env allowlist
- bridge networking by default (no host network)

## Environment Flags

- `PROGENITOR_CONTAINER_RUNNERS=true` enables containerized spawned runtimes
- `PROGENITOR_CONTAINER_ENGINE=docker|podman` selects runtime engine
- `PROGENITOR_AGENT_RUNNER_IMAGE=...` image for A2A
- `PROGENITOR_MCP_RUNNER_IMAGE=...` image for MCP
- `PROGENITOR_CONTAINER_NETWORK=bridge` network mode default
- `PROGENITOR_CONTAINER_ENV_ALLOWLIST=VAR1,VAR2,...` default host env pass-through
- `PROGENITOR_CONTAINER_RUN_DIR=...` override per-run bundle directory

## Networking Defaults

- Default is `bridge`
- No host network unless explicitly configured
- Host port mappings are only added when HTTP transport needs them
