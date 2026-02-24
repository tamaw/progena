#!/usr/bin/env bash
set -euo pipefail

: "${AGENT_NAME:?AGENT_NAME is required}"
: "${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY is required}"

PORT="${AGENT_PORT:-8080}"
if [ "${AGENT_NAME}" = "builder_agent" ]; then
  PORT=8107
fi

AGENT_HOST="${AGENT_HOST:-progenitor-agent-${AGENT_NAME}}"
AGENT_URL="http://${AGENT_HOST}:${PORT}/v1"
MODEL_NAME="agent/${AGENT_NAME}"
MODEL_ID="agent-${AGENT_NAME}"
PROXY_MODEL="openai/${AGENT_NAME}"
MODEL_DESCRIPTION="Self-contained agent: ${AGENT_NAME}"

REGISTER_BASE_URL="${LITELLM_BASE_URL:-http://litellm:4000}"
if echo "$REGISTER_BASE_URL" | grep -Eq "://(localhost|127\\.0\\.0\\.1)"; then
  REGISTER_BASE_URL="http://litellm:4000"
fi

PAYLOAD=$(cat <<EOF
{
  "model_name": "${MODEL_NAME}",
  "litellm_params": {
    "model": "${PROXY_MODEL}",
    "api_base": "${AGENT_URL}",
    "api_key": "${LITELLM_MASTER_KEY}"
  },
  "model_info": {
    "description": "${MODEL_DESCRIPTION}",
    "id": "${MODEL_ID}"
  }
}
EOF
)

call_litellm() {
  local endpoint="$1"
  local response
  response=$(docker run --rm --network langfuse_network curlimages/curl:8.6.0 \
    -sS -X POST "${REGISTER_BASE_URL}${endpoint}" \
    -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
    -H "Content-Type: application/json" \
    -d "${PAYLOAD}" \
    -w '\n%{http_code}')
  HTTP_CODE=$(echo "${response}" | tail -n1)
  HTTP_BODY=$(echo "${response}" | sed '$d')
}

call_litellm "/model/new"
if [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]; then
  echo "Registered ${MODEL_NAME} with LiteLLM at ${AGENT_URL}"
  exit 0
fi
echo "LiteLLM /model/new failed (${HTTP_CODE}) for ${MODEL_NAME}"
echo "${HTTP_BODY}"

call_litellm "/model/update"
if [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]; then
  echo "Updated existing ${MODEL_NAME} with LiteLLM at ${AGENT_URL}"
  exit 0
fi
echo "LiteLLM /model/update failed (${HTTP_CODE}) for ${MODEL_NAME}"
echo "${HTTP_BODY}"
exit 1
