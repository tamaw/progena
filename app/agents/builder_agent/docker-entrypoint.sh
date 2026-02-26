#!/bin/sh
set -euo pipefail

ensure_dir() {
  mkdir -p "$1"
}

for target in /app/data /app/logs /app/worktrees; do
  ensure_dir "$target"
done
ensure_dir /app/logs/builder_agent

if [ "$(id -u)" -eq 0 ]; then
  # Best-effort ownership fix for mounted paths when starting as root.
  if ! chown -R runner:agent /app/data /app/logs /app/worktrees; then
    echo "WARN: failed to chown runtime directories; continuing with existing ownership" >&2
  fi
  exec runuser -u runner -- "$@"
fi

# Container already started as non-root (--user in workflow); run directly.
exec "$@"
