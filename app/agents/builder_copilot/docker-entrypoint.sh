#!/bin/sh
set -eu

ensure_dir() {
  mkdir -p "$1"
}

for target in /app/data /app/logs /app/worktrees; do
  ensure_dir "$target"
done
ensure_dir /app/logs/builder_copilot

if [ "${PROGENITOR_COPILOT_ACP_BACKGROUND:-true}" = "true" ]; then
  COPILOT_BIN="${PROGENITOR_COPILOT_BIN:-copilot}"
  COPILOT_MODEL="${PROGENITOR_COPILOT_MODEL:-gpt-5.3-codex}"
  ACP_LOG_FILE="${PROGENITOR_COPILOT_ACP_LOG_FILE:-/app/logs/builder_copilot/copilot-acp.background.log}"
  ACP_PID_FILE="${PROGENITOR_COPILOT_ACP_PID_FILE:-/app/logs/builder_copilot/copilot-acp.background.pid}"

  # Keep stdin open so ACP stays alive as a background process for the container lifetime.
  (tail -f /dev/null | "$COPILOT_BIN" --acp --yolo --model "$COPILOT_MODEL" >>"$ACP_LOG_FILE" 2>&1) &
  ACP_PID="$!"
  echo "$ACP_PID" > "$ACP_PID_FILE"
  echo "Started background Copilot ACP (pid=$ACP_PID, model=$COPILOT_MODEL)"
fi

exec "$@"
