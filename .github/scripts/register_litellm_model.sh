#!/usr/bin/env bash
set -euo pipefail

: "${AGENT_NAME:?AGENT_NAME is required}"
: "${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY is required}"

PORT="${AGENT_PORT:-8080}"
if [ "${AGENT_NAME}" = "builder_agent" ]; then
  PORT=8107
fi

AGENT_HOST="${AGENT_HOST:-progenitor-agent-${AGENT_NAME}}"
AGENT_URL="http://${AGENT_HOST}:${PORT}/"
AGENT_CARD_URL="${AGENT_URL}.well-known/agent.json"

REGISTER_BASE_URL="${LITELLM_BASE_URL:-http://litellm:4000}"
if echo "$REGISTER_BASE_URL" | grep -Eq "://(localhost|127\\.0\\.0\\.1)"; then
  REGISTER_BASE_URL="http://litellm:4000"
fi
REGISTER_BASE_URL="${REGISTER_BASE_URL%/}"
if echo "$REGISTER_BASE_URL" | grep -Eq "/v1$"; then
  AGENTS_URL="${REGISTER_BASE_URL}/agents"
else
  AGENTS_URL="${REGISTER_BASE_URL}/v1/agents"
fi

call_litellm() {
  local method="$1"
  local url="$2"
  local payload="${3:-}"
  local response
  if [ -n "$payload" ]; then
    response=$(docker run --rm --network langfuse_network curlimages/curl:8.6.0 \
      -sS -X "$method" "$url" \
      -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
      -H "Content-Type: application/json" \
      -d "$payload" \
      -w '\n%{http_code}')
  else
    response=$(docker run --rm --network langfuse_network curlimages/curl:8.6.0 \
      -sS -X "$method" "$url" \
      -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
      -H "Content-Type: application/json" \
      -w '\n%{http_code}')
  fi
  HTTP_CODE=$(echo "${response}" | tail -n1)
  HTTP_BODY=$(echo "${response}" | sed '$d')
}

is_success() {
  [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]
}

extract_agent_id() {
  local compact
  compact=$(echo "$1" | tr -d '\n\r\t ')
  local id
  id=$(echo "$compact" | sed -n "s/.*\"agent_name\":\"${AGENT_NAME}\"[^}]*\"agent_id\":\"\\([^\"]*\\)\".*/\\1/p")
  if [ -n "$id" ]; then
    echo "$id"
    return 0
  fi
  id=$(echo "$compact" | sed -n "s/.*\"agent_id\":\"\\([^\"]*\\)\"[^}]*\"agent_name\":\"${AGENT_NAME}\".*/\\1/p")
  echo "$id"
}

call_litellm "GET" "$AGENT_CARD_URL"
if ! is_success; then
  echo "Failed to fetch agent card from ${AGENT_CARD_URL} (${HTTP_CODE})"
  echo "${HTTP_BODY}"
  exit 1
fi
AGENT_CARD_JSON="${HTTP_BODY}"

AGENT_CONFIG_PAYLOAD=$(cat <<EOF
{
  "agent_name": "${AGENT_NAME}",
  "agent_card_params": ${AGENT_CARD_JSON},
  "litellm_params": {
    "make_public": true
  }
}
EOF
)

AGENT_WRAPPED_PAYLOAD=$(cat <<EOF
{
  "agent": {
    "agent_name": "${AGENT_NAME}",
    "agent_card_params": ${AGENT_CARD_JSON},
    "litellm_params": {
      "make_public": true
    }
  }
}
EOF
)

call_litellm "POST" "$AGENTS_URL" "$AGENT_CONFIG_PAYLOAD"
if is_success; then
  echo "Registered ${AGENT_NAME} via ${AGENTS_URL}"
  exit 0
fi
echo "LiteLLM POST ${AGENTS_URL} failed (${HTTP_CODE})"
echo "${HTTP_BODY}"

call_litellm "POST" "$AGENTS_URL" "$AGENT_WRAPPED_PAYLOAD"
if is_success; then
  echo "Registered ${AGENT_NAME} via wrapped payload at ${AGENTS_URL}"
  exit 0
fi
echo "LiteLLM POST wrapped payload failed (${HTTP_CODE})"
echo "${HTTP_BODY}"

call_litellm "GET" "$AGENTS_URL"
if ! is_success; then
  echo "Failed to list agents for update fallback (${HTTP_CODE})"
  echo "${HTTP_BODY}"
  exit 1
fi

AGENT_ID=$(extract_agent_id "${HTTP_BODY}")
if [ -z "${AGENT_ID}" ]; then
  echo "Could not find existing agent_id for ${AGENT_NAME}"
  echo "${HTTP_BODY}"
  exit 1
fi

call_litellm "PATCH" "${AGENTS_URL}/${AGENT_ID}" "$AGENT_CONFIG_PAYLOAD"
if is_success; then
  echo "Patched existing agent ${AGENT_NAME} (${AGENT_ID})"
  exit 0
fi
echo "LiteLLM PATCH ${AGENTS_URL}/${AGENT_ID} failed (${HTTP_CODE})"
echo "${HTTP_BODY}"

call_litellm "PUT" "${AGENTS_URL}/${AGENT_ID}" "$AGENT_CONFIG_PAYLOAD"
if is_success; then
  echo "Updated existing agent ${AGENT_NAME} (${AGENT_ID})"
  exit 0
fi
echo "LiteLLM PUT ${AGENTS_URL}/${AGENT_ID} failed (${HTTP_CODE})"
echo "${HTTP_BODY}"
exit 1
