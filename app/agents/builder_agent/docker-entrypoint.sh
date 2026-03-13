#!/bin/sh
set -euo pipefail

export COPILOT_GITHUB_TOKEN="${COPILOT_GITHUB_TOKEN:-}"
export GH_TOKEN="${GH_TOKEN:-}"
export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-}"
export GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-}"
export AGENT_NAME="${AGENT_NAME:-builder_agent}"

ensure_dir() {
  mkdir -p "$1"
}

for target in /app/data /app/logs /app/worktrees; do
  ensure_dir "$target"
done
ensure_dir "${PROGENITOR_LOG_DIR:-/app/logs}"
ensure_dir "${PROGENITOR_LOG_DIR:-/app/logs}/${AGENT_NAME}"

if [ -n "${GH_TOKEN:-}" ]; then
  git config --global --replace-all credential.helper "!gh auth git-credential" 2>/dev/null || true
fi

git config --global --add safe.directory /app/worktrees 2>/dev/null || true
git config --global --add safe.directory "/app/worktrees/*" 2>/dev/null || true

if [ -n "${GIT_AUTHOR_NAME:-}" ]; then
  git config --global user.name "$GIT_AUTHOR_NAME" 2>/dev/null || true
fi
if [ -n "${GIT_AUTHOR_EMAIL:-}" ]; then
  git config --global user.email "$GIT_AUTHOR_EMAIL" 2>/dev/null || true
fi

if [ -n "${GH_TOKEN:-}" ] && command -v gh >/dev/null 2>&1; then
  if ! gh auth setup-git --hostname github.com 2>/dev/null; then
    echo "WARN: gh auth setup-git failed, git will fall back to configured credential helper" >&2
  fi
fi

if [ "$(id -u)" -eq 0 ]; then
  # Best-effort ownership fix for mounted paths when starting as root.
  if ! chown -R runner:agent /app/data /app/logs /app/worktrees; then
    echo "WARN: failed to chown runtime directories; continuing with existing ownership" >&2
  fi
  exec runuser -u runner -- "$@"
fi

# Container already started as non-root (--user in workflow); run directly.
exec "$@"
