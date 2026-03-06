#!/bin/sh
set -eu

# Ensure auth tokens are exported for copilot subprocess inheritance
export COPILOT_GITHUB_TOKEN="${COPILOT_GITHUB_TOKEN:-}"
export GH_TOKEN="${GH_TOKEN:-}"
export GITHUB_TOKEN="${GITHUB_TOKEN:-}"

mkdir -p /app/logs

exec "$@"
