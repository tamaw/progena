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
  AGENT_NAME="builder_copilot"
  BASE_LOG_DIR="${PROGENITOR_LOG_DIR:?PROGENITOR_LOG_DIR must be set}"
  ACP_STDOUT_LOG_FILE="${BASE_LOG_DIR}/${AGENT_NAME}/${AGENT_NAME}.stdout.log"
  ACP_STDERR_LOG_FILE="${BASE_LOG_DIR}/${AGENT_NAME}/${AGENT_NAME}.stderr.log"
  ACP_PID_FILE="${PROGENITOR_COPILOT_ACP_PID_FILE:-${BASE_LOG_DIR}/copilot-acp.background.pid}"

  ensure_dir "$(dirname "$ACP_STDOUT_LOG_FILE")"
  ensure_dir "$(dirname "$ACP_STDERR_LOG_FILE")"
  ensure_dir "$(dirname "$ACP_PID_FILE")"
  echo "ACP stdout log: $ACP_STDOUT_LOG_FILE"
  echo "ACP stderr log: $ACP_STDERR_LOG_FILE"
  echo "ACP pid file: $ACP_PID_FILE"

  # Keep stdin open so ACP stays alive as a background process for the container lifetime.
  (tail -f /dev/null | "$COPILOT_BIN" --acp --yolo --model "$COPILOT_MODEL" >>"$ACP_STDOUT_LOG_FILE" 2>>"$ACP_STDERR_LOG_FILE") &
  ACP_PID="$!"
  echo "$ACP_PID" > "$ACP_PID_FILE"
  echo "Started background Copilot ACP (pid=$ACP_PID, model=$COPILOT_MODEL)"
fi

exec "$@"
