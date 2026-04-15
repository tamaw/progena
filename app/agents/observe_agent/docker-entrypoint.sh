#!/bin/sh
set -eu

agent_name="${AGENT_NAME:-observe_agent}"
data_dir="${PROGENITOR_DATA_DIR:-/app/data}"
log_dir="${PROGENITOR_LOG_DIR:-/app/logs}"
state_dir="${PROGENITOR_STATE_DIR:-$data_dir/state}"
worktrees_root="${PROGENITOR_WORKTREES_ROOT:-/app/worktrees}"
prompts_dir="${PROGENITOR_PROMPTS_DIR:-$data_dir/prompts}"
skills_dir="${data_dir}/skills"

mkdir -p "$data_dir" "$log_dir" "$log_dir/$agent_name" "$state_dir" "$worktrees_root" "$prompts_dir" "$skills_dir" /app/tmp /home/runner

if command -v git >/dev/null 2>&1; then
  git config --global --add safe.directory "$worktrees_root" || true
  git config --global --add safe.directory "$worktrees_root/*" || true
fi

exec "$@"
