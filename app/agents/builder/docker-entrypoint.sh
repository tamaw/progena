#!/bin/sh
set -eu

ensure_dir() {
  mkdir -p "$1"
}

for target in /app/data /app/logs /app/worktrees; do
  ensure_dir "$target"
done
ensure_dir /app/logs/builder

exec "$@"
