#!/usr/bin/env bash
set -euo pipefail

export HOME=/home/claude
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
export CLAUDE_WORKSPACE="${CLAUDE_WORKSPACE:-/app/repos}"
export CLAUDE_ACP_PORT="${CLAUDE_ACP_PORT:-3001}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"
export XDG_STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"

ensure_dirs() {
    mkdir -p \
        "${CLAUDE_CONFIG_DIR}" \
        "${CLAUDE_WORKSPACE}" \
        "${XDG_CONFIG_HOME}" \
        "${XDG_CACHE_HOME}" \
        "${XDG_STATE_HOME}"
}

if [[ "$(id -u)" -eq 0 ]]; then
    ensure_dirs
    chown -R claude:claude "${HOME}"
    chown claude:claude "${CLAUDE_WORKSPACE}" 2>/dev/null || true
    exec runuser -u claude -- env \
        HOME="${HOME}" \
        CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR}" \
        CLAUDE_WORKSPACE="${CLAUDE_WORKSPACE}" \
        CLAUDE_ACP_PORT="${CLAUDE_ACP_PORT}" \
        XDG_CONFIG_HOME="${XDG_CONFIG_HOME}" \
        XDG_CACHE_HOME="${XDG_CACHE_HOME}" \
        XDG_STATE_HOME="${XDG_STATE_HOME}" \
        "$0" "$@"
fi

ensure_dirs

git config --global --add safe.directory "${CLAUDE_WORKSPACE}" 2>/dev/null || true
git config --global --add safe.directory "${CLAUDE_WORKSPACE}/*" 2>/dev/null || true
# Claude commits its own work in Progena's worktrees; pushing stays with
# Progena's repo-manager agent (this container has no GitHub credentials).
git config --global user.name "${CLAUDE_GIT_NAME:-Claude (Progena)}"
git config --global user.email "${CLAUDE_GIT_EMAIL:-claude@progena.invalid}"

if [[ "$#" -gt 0 ]]; then
    exec "$@"
fi

# Build the claude-agent-acp command string (with any extra args)
acp_cmd="claude-agent-acp"
if [[ -n "${CLAUDE_EXTRA_ARGS:-}" ]]; then
    acp_cmd="claude-agent-acp ${CLAUDE_EXTRA_ARGS}"
fi

# claude-agent-acp uses stdio transport; expose it over TCP using socat so
# callers can connect without docker exec. Each TCP connection spawns a fresh
# claude-agent-acp process, which matches the ACP session model. Its stderr goes
# to the container log; mixing it into the socket corrupts the JSON-RPC stream.
echo "[claude-entrypoint] Listening on 0.0.0.0:${CLAUDE_ACP_PORT} (socat -> claude-agent-acp)" >&2
exec socat "TCP-LISTEN:${CLAUDE_ACP_PORT},bind=0.0.0.0,reuseaddr,fork" \
    "EXEC:${acp_cmd}"
