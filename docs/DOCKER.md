# Docker stack

This document covers the main Progena container stack: Postgres, runtime, backend,
frontend, and the internal Claude ACP sidecar.

## What the repo currently ships

`docker/docker-compose.progena.yml` defines a five-container stack:
- `postgres` - PostgreSQL with pgvector
- `progena` - runs `python -m src.progena.main`
- `backend` - runs `uvicorn src.backend.main:app`
- `frontend` - builds the Vite app and serves it with nginx
- `claude` - provides Claude Code over internal ACP transport

Supporting Dockerfiles:
- `docker/progena/Dockerfile`
- `docker/backend/Dockerfile`
- `docker/frontend/Dockerfile`
- `docker/claude/Dockerfile`

Standalone A2A services, their images, and their deployment workflows are owned by
[progena.agents](https://github.com/tamaw/progena.agents), not this repository.
Progena can discover an external checkout through a Compose override that mounts it
read-only into the runtime and sets `PROGENA_AGENT_REPOSITORIES` to its
in-container path. No external repository is required for Progena or its built-in
object agents.

## Before starting

Model connections are configured in **Settings → Providers**, not required endpoint environment variables. Foundry, OpenCode Go, and ChatGPT plan connections use the Responses API.

For database-stored API keys, generate one Fernet key and set `PROGENA_PROVIDER_ENCRYPTION_KEY` in the deployment `.env`; Compose passes it to backend and runtime:

```bash
# Activate the project virtual environment first.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep that key outside version control and retain it across restarts. Alternatively select environment credentials using `AZURE_OPENAI_API_KEY` or `OPENCODE_GO_API_KEY`. Custom `PROGENA_PROVIDER_*_API_KEY` names must be passed explicitly to both containers.

Provider tables live in `BACKEND_DATABASE_NAME` (default `backend`) on the configured PostgreSQL server; both services need access to it. Compose must give both services the same database name and encryption key. Existing `AZURE_OPENAI_BASE_URL` and `PROGENA_MODEL_DEFAULT` are imported once on initial provider-table setup; subsequent connection edits come from the database. An installation with no configured provider starts normally but rejects model calls until configured.

Common optional overrides:
- `PROGENA_MODEL_DEFAULT`
- `GH_TOKEN`
- `PROGENA_REASONING_ENABLED`
- `PROGENA_REASONING_EFFORT`
- `PROGENA_REASONING_SUMMARY`
- `TELEGRAM_*` backend settings

Foundry starts with reasoning enabled by default. `PROGENA_REASONING_EFFORT`
controls the default effort and `PROGENA_REASONING_SUMMARY` the visible reasoning
summary preference. These Foundry-specific overrides are not sent to OpenCode Go.

The compose file also expects the external Docker network `progena_network`
and the external volume `progena-shared` to exist:

```bash
docker network create progena_network
docker volume create progena-shared
```

`progena-shared` is mounted at `/shared` in the runtime and exposed to the
agent as the writable `/shared/` route (`PROGENA_SHARED_DIR`). Mount the same
volume into A2A agent containers to exchange files with them; nothing in it is
access-controlled, so any container that mounts it can change anything there.

### ChatGPT plan OAuth on a self-hosted server

ChatGPT plan authentication is a model-provider connection, not Progena's
application login. It uses OpenAI's supported open-source flow; see the official
[sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
and [self-hosted server](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
guides.

Set the same persistent `PROGENA_PROVIDER_ENCRYPTION_KEY` on backend and runtime
before importing credentials. Access, refresh, and retained ID tokens are encrypted
in the backend provider database. Back up that database and encryption key
securely together; neither belongs in source control or application transcripts.

On the computer running your browser, activate the project venv and sign in:

```bash
python -m src.progena.settings.chatgpt_cli sign-in \
  --credential-file "$HOME/.config/progena/chatgpt-credentials.json"
```

The helper opens the system browser and listens only on `127.0.0.1` at an
available port. Do not replace its callback with the public server URL or
`localhost`. It validates state, PKCE, the signed ID token, and the account
identity. Credential files are written atomically with Unix mode `0600`.
Use a different credential file for each registration; use `--reauthorize`
with its existing file to reconnect that registration. Routine reauthorization
does not request consent. If a provider reports that ChatGPT plan usage is
disabled, explicitly request it again with:

```bash
python -m src.progena.settings.chatgpt_cli sign-in \
  --reauthorize --enable-plan-usage \
  --credential-file "$HOME/.config/progena/chatgpt-credentials.json"
```

Transfer the selected protected file over SSH to a private directory on the
server. For a Compose installation, stream it into the runtime's writable tmpfs
as its unprivileged runtime user (`docker cp` cannot write to the read-only
root filesystem or its tmpfs, and would create a root-owned file), then import it:

```bash
# On the server after the SSH transfer; substitute your protected file path.
docker exec -i -u 10001 progena sh -c 'umask 077; cat > /app/tmp/chatgpt-credentials.json' \
  < /private/path/chatgpt-credentials.json
docker exec progena python -m src.progena.settings.chatgpt_cli import \
  --credential-file /app/tmp/chatgpt-credentials.json --name "My ChatGPT plan"
docker exec progena rm /app/tmp/chatgpt-credentials.json
```

Remove the server-side transfer copy after a successful import. Do not put token
contents in shell arguments, Settings fields, tickets, or chat. The server retains
its own stable host ID and owns subsequent token refreshes; do not run competing
refresh clients against the transferred session. For reconnecting, import the
new credential file with `--provider-id` set to the existing provider ID.

Reload **Settings → Providers**, select a model from that account's catalogue,
then select the connection as the default or assign it to a model profile.
Separate provider records keep saved registrations distinct, including those
with the same email address. Disconnect stops further use and attempts renewable
session revocation. Check ChatGPT Settings if remote revocation cannot be confirmed.

Install the declared OpenAI SDK range (`>=3.37.0,<4.0.0`); older SDKs do
not understand namespace tools and can misparse response tool metadata.

ChatGPT requests stream with `store:false`, include application-managed context,
and omit unsupported controls such as temperature and output-token limits. Its
terminal Responses event can omit items emitted earlier in the stream, so Progena
retains completed streamed items for auxiliary calls. Tools and MCP execute through
Progena. OpenAI-hosted file search, Code Interpreter, image generation, hosted MCP,
audio/video, and transcription are not supported by this route. Local file handling
and checkpoints remain Progena-owned.
`store:false` disables Responses storage; it is not a general zero-retention
guarantee. Plan limits are not API token prices, and errors never silently switch
to another billing connection. Review
[OpenAI's current limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
and [usage settings](https://chatgpt.com/settings/usage).


## Start and stop

From the repository root:

```bash
docker compose -f docker/docker-compose.progena.yml up --build -d
docker compose -f docker/docker-compose.progena.yml down
```

Useful inspection commands:

```bash
docker compose -f docker/docker-compose.progena.yml ps
docker compose -f docker/docker-compose.progena.yml logs -f
docker compose -f docker/docker-compose.progena.yml logs -f progena
docker compose -f docker/docker-compose.progena.yml logs -f backend
docker compose -f docker/docker-compose.progena.yml logs -f frontend
docker compose -f docker/docker-compose.progena.yml logs -f claude
```

## Network and ports

Current compose behavior:
- Only the frontend is published to the host, bound to localhost: `127.0.0.1:8081:80` over HTTP
- The backend listens on port `8000` over HTTP **inside** the compose networks only
- The Progena HTTP server listens on port `9501` inside the compose networks
- Claude ACP listens at `tcp://progena-agent-claude:3001` inside the compose networks only
- The backend connects to the runtime with `PROGENA_RUNTIME_URL=http://progena:9501`
- The compose file attaches services to an internal bridge network and the external `progena_network`

For public deployment, terminate TLS and enforce authentication at a trusted
reverse proxy outside this Compose project. Forward requests to
`http://127.0.0.1:8081`; keep certificates and authentication credentials outside
the repository.

When upgrading from the old HTTPS containers, switch the reverse-proxy upstream
from HTTPS to HTTP together with the frontend container port change from 443 to
80. Public client traffic should remain HTTPS.

The localhost binding prevents direct remote access to the frontend; the backend
and runtime remain internal to the Docker networks.
Frontend nginx preserves the host proxy's `X-Forwarded-Proto: https` header,
and Uvicorn trusts forwarded headers on its Docker-internal port so redirects
retain the public HTTPS scheme. Do not publish the backend directly.


If you need direct host access to backend port `8000`, add your own port mapping
to a one-off override file; do not expose it in the public deployment.

## Volumes and ephemeral storage

- `progena` and `backend`: `${PROGENA_HOST_LOGS_DIR:-../logs}:/app/logs`, `${PROGENA_HOST_REPOS_DIR:-../repos}:/app/repos`
- `claude`: `${PROGENA_HOST_CLAUDE_DIR:-../.claude-agent}:/home/claude/.claude`
- `backend` only: the same directory is mounted read-only at
  `/var/lib/progena/claude` for task-bound ACP transcript detail reads. The
  runtime never receives this mount; it asks the backend over the internal
  `PROGENA_BACKEND_URL` and cannot browse Claude configuration. The reader
  resolves only the verified task's session file below `projects/`, rejects
  traversal and symlink escapes, and never exposes a general config-file API.
  Both mounts preserve the existing `../.claude-agent` host-directory default;
  deployments using `/var/lib/progena/claude` must keep their existing
  `PROGENA_HOST_CLAUDE_DIR` override. Adding the reader does not move user data.
- `backend`: `PROGENA_ACP_CAPTURE_DIR=/app/logs/acp-capture` keeps temporary
  lossless ACP output on the existing disk-backed logs volume, not `/app/tmp`
  tmpfs. Capture files are private anonymous files and close after a turn's
  result is materialized. Generic agents retain exact output in their task
  record; verified Claude sessions use native transcript evidence instead of
  retaining the full stream in memory. Interrupted turns retain the existing
  interruption semantics; anonymous capture is not a crash-recovery journal.

The CI deployment uses the GitHub Environment `production`. Store credentials in
its secrets. Containers, operational logs, and agent shells use UTC; user
wall-clock scheduling is stored in account preferences.

Ephemeral storage:
- `/app/tmp` is a `tmpfs` mount in `progena` and `backend`

Notes:
- Instructions and runtime memories are Postgres-backed content documents
  (`src/progena/documents/content.py`, keyed `instruction:`/`memory:` in
  the same shared `store` table as `mcp.json`/`schedule.json`/etc — see
  `src/progena/state/manager.py`). Neither `progena` nor `backend` mounts
  a host directory for them. `data/instructions/` is checked into git and
  `COPY`'d into the `progena` image (not `backend`'s); every boot syncs it
  into Postgres: top-level Markdown files retain logical paths such as
  `progena/genesis.md`. They are added when missing and otherwise kept,
  since they are edited on the Instructions page. Explicit subdirectories in
  custom instruction sources retain their own group names.
  Standalone A2A agents own their instructions, images, and service lifecycle in the
  separate [agents repository](https://github.com/tamaw/progena.agents); they are
  not copied into this image. `data/skills/` is a read-only disk mount (`/skills/`),
  not stored in Postgres.
  After upgrading to JavaScript support, review the saved
  `progena/genesis.md` on the Instructions page: older copies say Progena cannot
  execute code. Replace that blanket restriction with permission to use the
  `javascript` tool for computation, allowlisted reads/A2A communication and
  configured subagent dispatch, following `data/instructions/genesis.md`.
  Retain the direct host-code/shell/source-edit restrictions and review the
  bridge approval caveat in `docs/SPEC.md`. An image upgrade deliberately does
  not overwrite saved instructions.
- `current_state`, `schedule`, `registry/agents`, `mcp`, `knowledge`, `health`,
  `kanban`, `attachments`, `sessions`, `prompts`, `skills`, and `memories` all
  persist through the shared LangGraph/Postgres document store with no
  filesystem fallback anywhere in the stack. `data/schemas/` (static JSON
  Schema contracts read by `structured_output` and document types) is
  `COPY`'d into the images, so the schemas a container reads always match its
  code. It is not bind-mounted: the deploy's runner checkout is reused by later
  CI jobs on other branches, so a mount from it served whatever branch ran last.
- Repository/worktree state mounts from `${PROGENA_HOST_REPOS_DIR:-../repos}` to `/app/repos`.
- `PROGENA_WORKTREE_DIR` is set to `/app/repos` in `progena` and `backend`.

## Health checks

`/healthz` is the explicit Docker/container liveness exception to the
event-stream-only browser API. It returns an empty `200 OK` and no application
data.

Current health probes are implemented as follows (run the container-local probes with
`docker compose exec`; only the frontend is published on the host):
- `backend` container: `docker compose -f docker/docker-compose.progena.yml exec -T backend curl -fsS http://127.0.0.1:8000/healthz`
- `frontend` through the published nginx port: `curl -fsS http://127.0.0.1:8081/healthz` (proxied to `backend`)
- `frontend` container process: `docker compose -f docker/docker-compose.progena.yml exec -T frontend wget -q -O /dev/null http://127.0.0.1/`
- `progena` container: `docker compose -f docker/docker-compose.progena.yml exec -T progena curl -fsS http://127.0.0.1:9501/health` (the runtime's own HTTP endpoint)
- `claude` container: Docker probes its `socat` listener; the ACP endpoint is internal only.

## Self-hosted Actions runners

`docker/gha-runner/Dockerfile` builds the runner image with GitHub Actions runner
2.338.0, Node.js 24, Python 3.12, and the Docker CLI/Buildx plugin.
Build it from the repository root:

```bash
docker build -f docker/gha-runner/Dockerfile -t local/gha-runner:2.338.0 .
```

Repository-scoped runners need a separate registration for each repository, even
when their containers share a Docker host. Generate a short-lived registration
token using `gh api --method POST repos/OWNER/REPO/actions/runners/registration-token`;
never reuse another runner's credentials or commit tokens.

Use a distinct runner name, persistent registration volume, and work directory
per runner. Bind the work directory at the same absolute path on the host and
inside the container so Docker-based actions can mount job files correctly.
The container needs `/var/run/docker.sock`; this grants host-level Docker access,
so restrict it to trusted workflows. Deployment jobs require the `docker` label.

Configure the destination repository's deployment environment, secrets, variables,
and host mounts before allowing queued deployment jobs onto a new runner.

### Workflow trust boundary

The checked-in CI workflow runs its test jobs for pushes to `main`, manual runs
that select `main`, and pull requests into `main` whose head branch is in
`tamaw/progena` itself. Fork pull requests are skipped by the job conditions.
Only pushes to `main` and manual `main` runs build images and deploy. The
Docker-prune workflow accepts only scheduled or manual runs on that same
repository and branch. Pull-request test jobs run on the same persistent
runners as deployment, so they execute branch code there; treat write access to
the repository as trusted. Fork installations must deliberately configure their
own repository identity and runners.

External actions are pinned to full commit SHAs. Checkouts do not persist the
GitHub token in Git configuration, and workflow tokens have read-only contents
permission. Pin updates must be reviewed like executable dependency updates.
Configure the `production` Environment with a custom deployment branch policy
allowing only the branch `main` (not tags); the job condition alone is not an
independent secrets boundary.

Deployment health failures report container health status only. Application logs
and unrelated container listings are deliberately omitted from Actions output;
inspect application logs privately on the deployment host.

Repository Actions policy allows only the exact action commits used by the
checked-in workflows, with GitHub-owned/verified-publisher blanket allowances
disabled. Updating an action pin also requires updating that allowlist.
Log and artifact retention is 14 days. Retention changes apply to new data; they
do not retroactively purge existing logs, artifacts or copies downloaded by others.

The `Secret scan` workflow checks new PR and `main` push commits using a
checksum-verified Gitleaks binary and pinned upstream rules. It runs on
GitHub-hosted runners without application dependencies or production credentials,
redacts findings, and uploads no reports. Repository suppression files and inline
allow comments cannot bypass the scan. Manual dispatch scans the selected tip;
an optional `base_sha` scans the range after that commit. This contribution scan
does not replace a full-history audit before publication.

### Before making a repository public

Workflow conditions are defense in depth, not a sandbox: a pull request can
propose a different workflow. Never approve an untrusted workflow to run on
these self-hosted runners, even when GitHub requires approval.

1. Merge the reviewed hardening changes while the repository is still private.
   Follow the repository's explicit approval policy for merges and deployments.
2. Disable Actions before changing visibility. Keep private-fork workflows
   disabled and do not send fork workflows write tokens, secrets or variables.
3. After publication, require workflow approval for **all external contributors**,
   protect `main` with reviewed pull requests, and verify that `production` still
   allows deployments only from `main`. GitHub does not expose the public-fork
   contributor-approval setting while the repository is private.
4. Re-enable Actions only after those protections are verified. Contribution
   secret scanning runs on isolated GitHub-hosted runners without production
   credentials. Same-repository PR test jobs run on the self-hosted runners, so
   never approve a fork PR to run there, and do not use `pull_request_target`
   to execute PR code.

Scan all fetched Git refs before publication; deleting a secret from the current
tree does not remove it from history. The history contains retired frontend and
backend TLS private keys: never reuse those keys, and replace any still-active
certificate that uses them. Secret scanners are not proof that history contains
no credentials. Historic hostnames, private-network addresses and commit-author
email addresses will also become public; these are privacy metadata, not by
themselves authentication secrets.
