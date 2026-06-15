#!/usr/bin/env bash
set -euo pipefail

export HOME=/home/codex
export CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
export CODEX_WORKSPACE="${CODEX_WORKSPACE:-/app/repos}"
export CODEX_ACP_PORT="${CODEX_ACP_PORT:-3002}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"
export XDG_STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"

ensure_dirs() {
    mkdir -p \
        "${CODEX_HOME}" \
        "${CODEX_WORKSPACE}" \
        "${XDG_CONFIG_HOME}" \
        "${XDG_CACHE_HOME}" \
        "${XDG_STATE_HOME}"
}

if [[ "$(id -u)" -eq 0 ]]; then
    ensure_dirs
    chown -R codex:codex "${HOME}"
    chown codex:codex "${CODEX_WORKSPACE}" 2>/dev/null || true
    exec runuser -u codex -- env \
        HOME="${HOME}" \
        CODEX_HOME="${CODEX_HOME}" \
        CODEX_WORKSPACE="${CODEX_WORKSPACE}" \
        CODEX_ACP_PORT="${CODEX_ACP_PORT}" \
        OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
        XDG_CONFIG_HOME="${XDG_CONFIG_HOME}" \
        XDG_CACHE_HOME="${XDG_CACHE_HOME}" \
        XDG_STATE_HOME="${XDG_STATE_HOME}" \
        "$0" "$@"
fi

ensure_dirs

git config --global --add safe.directory "${CODEX_WORKSPACE}" 2>/dev/null || true
git config --global --add safe.directory "${CODEX_WORKSPACE}/*" 2>/dev/null || true

if [[ "$#" -gt 0 ]]; then
    exec "$@"
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "[codex-entrypoint] OPENAI_API_KEY not set; relying on persisted Codex login in ${CODEX_HOME}" >&2
fi

# Build codex-acp args — support config overrides via CODEX_ACP_ARGS
acp_cmd="codex-acp"
if [[ -n "${CODEX_ACP_ARGS:-}" ]]; then
    # Intentionally allow caller-controlled word splitting for additional CLI flags.
    # shellcheck disable=SC2206
    acp_cmd="codex-acp ${CODEX_ACP_ARGS}"
fi

# codex-acp is stdio-only; expose it over TCP via socat so agents can connect
# without docker exec. Each TCP connection spawns a fresh codex-acp process.
echo "[codex-entrypoint] Listening on 0.0.0.0:${CODEX_ACP_PORT} (socat -> codex-acp)" >&2
exec socat "TCP-LISTEN:${CODEX_ACP_PORT},bind=0.0.0.0,reuseaddr,fork" \
    "EXEC:${acp_cmd},stderr"
