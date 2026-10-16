#!/bin/sh
set -eu

agent_name="${AGENT_NAME:-greetings_agent}"
data_dir="${PROGENITOR_DATA_DIR:-/app/data}"
log_dir="${PROGENITOR_LOG_DIR:-/app/logs}"
state_dir="${PROGENITOR_STATE_DIR:-$data_dir/state}"
worktrees_root="${PROGENITOR_WORKTREES_ROOT:-/app/worktrees}"
instructions_dir="${PROGENITOR_INSTRUCTIONS_DIR:-$data_dir/instructions}"
skills_dir="${data_dir}/skills"

ensure_runtime_dirs() {
  mkdir -p "$data_dir" "$log_dir" "$log_dir/$agent_name" "$state_dir" "$worktrees_root" "$instructions_dir" "$skills_dir" /app/tmp /home/runner
}

if [ "$(id -u)" -eq 0 ]; then
  ensure_runtime_dirs
  if ! chown -R runner:agent "$data_dir" "$log_dir" "$worktrees_root" /app/tmp /home/runner; then
    echo "WARN: failed to chown runtime directories; continuing with existing ownership" >&2
  fi
  exec runuser -u runner -- "$0" "$@"
fi

ensure_runtime_dirs

if command -v git >/dev/null 2>&1; then
  git config --global --add safe.directory "$worktrees_root" || true
  git config --global --add safe.directory "$worktrees_root/*" || true
fi

exec "$@"
