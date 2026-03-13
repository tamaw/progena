#!/bin/sh
set -eu

# Export tokens: GH_TOKEN for git operations, COPILOT_GITHUB_TOKEN for Copilot CLI
export COPILOT_GITHUB_TOKEN="${COPILOT_GITHUB_TOKEN:-}"
export GH_TOKEN="${GH_TOKEN:-}"
export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-}"
export GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-}"

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh CLI is not installed in container" >&2
  exit 1
fi

# Configure git to use gh CLI for authentication (if GH_TOKEN is available)
if [ -n "${GH_TOKEN:-}" ]; then
  # Use --replace-all to set a single global credential helper for all HTTPS URLs
  git config --global --replace-all credential.helper "!gh auth git-credential" 2>/dev/null || true
fi

ensure_workspace() {
  agent_name="${AGENT_NAME:?AGENT_NAME must be set}"
  base_dir="${PROGENITOR_WORKTREES_ROOT:-/app/worktrees}"
  repo_url="${PROGENITOR_REPO_URL:-}"
  token="${GH_TOKEN:-}"
  auth_repo_url="$repo_url"
  log_dir="${PROGENITOR_LOG_DIR:-/app/logs}"

  mkdir -p "$log_dir" "$log_dir/$agent_name" /app/data "$base_dir"

  # Containers often mount host-owned directories, which triggers Git's
  # ownership protection for non-root users. Mark our workspace as safe.
  git config --global --add safe.directory "$base_dir" || true
  git config --global --add safe.directory "$base_dir/*" || true

  # Configure commit author identity from runtime-injected environment variables.
  if [ -n "$GIT_AUTHOR_NAME" ]; then
    git config --global user.name "$GIT_AUTHOR_NAME" || true
  fi
  if [ -n "$GIT_AUTHOR_EMAIL" ]; then
    git config --global user.email "$GIT_AUTHOR_EMAIL" || true
  fi

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
