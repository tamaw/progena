#!/bin/sh
set -euo pipefail

ensure_dir() {
  mkdir -p "$1"
}

for target in /app/data /app/logs /app/worktrees; do
  ensure_dir "$target"
done
ensure_dir /app/logs/builder_agent

chown -R runner:agent /app/data /app/logs /app/worktrees

exec runuser -u runner -- "$@"
