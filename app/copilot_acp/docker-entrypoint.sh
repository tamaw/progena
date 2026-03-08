#!/bin/sh
set -eu

# Export tokens: GH_TOKEN for git operations, COPILOT_GITHUB_TOKEN for Copilot CLI
export COPILOT_GITHUB_TOKEN="${COPILOT_GITHUB_TOKEN:-}"
export GH_TOKEN="${GH_TOKEN:-}"

ensure_workspace() {
	base_dir="${PROGENITOR_PROJECT_ROOT:-/app/worktrees}"
	repo_url="${PROGENITOR_REPO_URL:-}"
	token="${GH_TOKEN:-}"
	auth_repo_url="$repo_url"
		log_dir="${PROGENITOR_LOG_DIR:-/app/logs/copilot_acp}"

		mkdir -p "$log_dir" /app/data "$base_dir"
	if [ -z "$repo_url" ]; then
		echo "WARN: PROGENITOR_REPO_URL is empty; skipping workspace bootstrap" >&2
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

	# Configure bare repo for worktrees
	git -C "$base_dir/.git" config core.bare false
	git -C "$base_dir/.git" config core.worktree "$base_dir"
	
	echo "Git worktree base initialized at ${base_dir}" >&2
}

ensure_workspace

if [ "$#" -gt 0 ]; then
	exec "$@"
fi

COPILOT_BIN="${PROGENITOR_COPILOT_BIN:-copilot}"
COPILOT_MODEL="${PROGENITOR_COPILOT_MODEL:-gpt-5.3-codex}"
COPILOT_FLAGS="${PROGENITOR_COPILOT_FLAGS:---yolo}"
ACP_PORT="${COPILOT_ACP_PORT:-3000}"
LOG_DIR="${PROGENITOR_LOG_DIR:-/app/logs/copilot_acp}"

COPILOT_CMD="$COPILOT_BIN --acp --port $ACP_PORT $COPILOT_FLAGS --model $COPILOT_MODEL"
ACP_LOG="${LOG_DIR}/copilot_acp.log"

echo "Starting Copilot ACP server on port ${ACP_PORT}" | tee -a "$ACP_LOG"
echo "Copilot command: ${COPILOT_CMD}" >> "$ACP_LOG"

exec sh -lc "${COPILOT_CMD}" >> "$ACP_LOG" 2>&1
