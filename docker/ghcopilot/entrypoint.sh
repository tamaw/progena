#!/usr/bin/env bash
set -euo pipefail

export HOME=/home/copilot
export COPILOT_HOME="${COPILOT_HOME:-${HOME}/.copilot}"
export COPILOT_WORKSPACE="${COPILOT_WORKSPACE:-/app/repos}"
export COPILOT_ACP_PORT="${COPILOT_ACP_PORT:-3000}"
export COPILOT_ACP_INTERNAL_PORT="${COPILOT_ACP_INTERNAL_PORT:-3001}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"
export XDG_STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"

ensure_dirs() {
    mkdir -p \
        "${COPILOT_HOME}" \
        "${COPILOT_WORKSPACE}" \
        "${XDG_CONFIG_HOME}" \
        "${XDG_CACHE_HOME}" \
        "${XDG_STATE_HOME}"
}

if [[ "$(id -u)" -eq 0 ]]; then
    ensure_dirs
    chown -R copilot:copilot "${HOME}"
    chown copilot:copilot "${COPILOT_WORKSPACE}" 2>/dev/null || true
    exec runuser -u copilot -- env \
        HOME="${HOME}" \
        COPILOT_HOME="${COPILOT_HOME}" \
        COPILOT_WORKSPACE="${COPILOT_WORKSPACE}" \
        XDG_CONFIG_HOME="${XDG_CONFIG_HOME}" \
        XDG_CACHE_HOME="${XDG_CACHE_HOME}" \
        XDG_STATE_HOME="${XDG_STATE_HOME}" \
        "$0" "$@"
fi

ensure_dirs

export COPILOT_GITHUB_TOKEN="${COPILOT_GITHUB_TOKEN:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
export GH_TOKEN="${GH_TOKEN:-${COPILOT_GITHUB_TOKEN}}"
export GITHUB_TOKEN="${GITHUB_TOKEN:-${COPILOT_GITHUB_TOKEN}}"
export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-}"
export GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-}"

if [[ -z "${COPILOT_GITHUB_TOKEN}" ]]; then
    echo "ERROR: set COPILOT_GITHUB_TOKEN before starting Copilot ACP" >&2
    exit 64
fi

git config --global --add safe.directory "${COPILOT_WORKSPACE}" 2>/dev/null || true
git config --global --add safe.directory "${COPILOT_WORKSPACE}/*" 2>/dev/null || true

if [[ -n "${GIT_AUTHOR_NAME}" ]]; then
    git config --global user.name "${GIT_AUTHOR_NAME}" 2>/dev/null || true
fi

if [[ -n "${GIT_AUTHOR_EMAIL}" ]]; then
    git config --global user.email "${GIT_AUTHOR_EMAIL}" 2>/dev/null || true
fi

if [[ "$#" -gt 0 ]]; then
    exec "$@"
fi

args=(--acp --yolo --add-dir "${COPILOT_WORKSPACE}")
args+=(--no-ask-user --no-auto-update)

if [[ -n "${COPILOT_ACP_PORT}" ]]; then
    args+=(--port "${COPILOT_ACP_INTERNAL_PORT}")
else
    args+=(--stdio)
fi

if [[ -n "${COPILOT_MODEL:-}" ]]; then
    args+=(--model "${COPILOT_MODEL}")
fi

if [[ -n "${COPILOT_REASONING_EFFORT:-}" ]]; then
    args+=(--reasoning-effort "${COPILOT_REASONING_EFFORT}")
fi

if [[ -n "${COPILOT_EXTRA_ARGS:-}" ]]; then
    # Intentionally allow caller-controlled word splitting for additional CLI flags.
    # shellcheck disable=SC2206
    extra_args=(${COPILOT_EXTRA_ARGS})
    args+=("${extra_args[@]}")
fi

if [[ -n "${COPILOT_ACP_PORT}" ]]; then
    copilot "${args[@]}" &
    copilot_pid=$!

    cleanup() {
        kill "${copilot_pid}" 2>/dev/null || true
    }
    trap cleanup EXIT INT TERM

    exec socat "TCP-LISTEN:${COPILOT_ACP_PORT},bind=0.0.0.0,reuseaddr,fork" "TCP:127.0.0.1:${COPILOT_ACP_INTERNAL_PORT}"
fi

exec copilot "${args[@]}"
