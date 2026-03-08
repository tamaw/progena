#!/bin/sh
set -eu

# Export tokens: GH_TOKEN for git operations, COPILOT_GITHUB_TOKEN for Copilot CLI
export COPILOT_GITHUB_TOKEN="${COPILOT_GITHUB_TOKEN:-}"
export GH_TOKEN="${GH_TOKEN:-}"

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh CLI is not installed in container" >&2
  exit 1
fi

ensure_workspace() {
  base_dir="${PROGENITOR_WORKTREES_ROOT:-/app/worktrees}"
  repo_url="${PROGENITOR_REPO_URL:-}"
  token="${GH_TOKEN:-}"
  auth_repo_url="$repo_url"
  log_dir="${PROGENITOR_LOG_DIR:-/app/logs/builder}"

  mkdir -p "$log_dir" /app/data "$base_dir"
  if [ -z "$repo_url" ]; then
    echo "WARN: PROGENITOR_REPO_URL is empty; skipping workspace bootstrap" >&2
    return
  fi

  if [ -d "$base_dir/.git" ]; then
    return
  fi

  case "$repo_url" in
    https://github.com/*)
      if [ -n "$token" ]; then
        auth_repo_url="https://x-access-token:${token}@github.com/${repo_url#https://github.com/}"
      fi
      ;;
  esac

  echo "Cloning ${repo_url} as bare repo into ${base_dir}/.git" >&2
  if ! git clone --bare "$auth_repo_url" "$base_dir/.git"; then
    echo "WARN: bare clone failed for ${repo_url}; continuing without preloaded repo" >&2
    return
  fi

  git -C "$base_dir/.git" config core.bare false
  git -C "$base_dir/.git" config core.worktree "$base_dir"
  echo "Git worktree base initialized at ${base_dir}" >&2
}

ensure_workspace

exec "$@"
