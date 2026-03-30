#!/bin/sh
set -eu

agent_name="${AGENT_NAME:-observe_agent}"
log_dir="${PROGENITOR_LOG_DIR:-/app/logs}"
state_dir="${PROGENITOR_STATE_DIR:-/app/data/state}"
worktrees_root="${PROGENITOR_WORKTREES_ROOT:-/app/worktrees}"
prompts_dir="${PROGENITOR_PROMPTS_DIR:-/app/prompts}"

mkdir -p "$log_dir" "$log_dir/$agent_name" "$state_dir" "$worktrees_root" "$prompts_dir" /app/tmp /home/runner

if command -v git >/dev/null 2>&1; then
  git config --global --add safe.directory "$worktrees_root" || true
  git config --global --add safe.directory "$worktrees_root/*" || true
fi

exec "$@"
