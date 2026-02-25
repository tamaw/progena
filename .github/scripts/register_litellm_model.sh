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
if echo "$REGISTER_BASE_URL" | grep -Eq "/v1$"; then
  A2A_BASE_URL="${REGISTER_BASE_URL}"
else
  A2A_BASE_URL="${REGISTER_BASE_URL}/v1"
fi

MODE="${1:-register}"
if [ "$MODE" != "register" ] && [ "$MODE" != "--validate-only" ]; then
  echo "Unknown mode: $MODE"
  echo "Usage: register_litellm_model.sh [register|--validate-only]"
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required on runner"
  exit 1
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
  local body="$1"
  echo "$body" | jq -r --arg name "$AGENT_NAME" '
    if type=="array" then
      map(select(.agent_name == $name and (.agent_id|type=="string"))) | .[0].agent_id // ""
    elif type=="object" then
      if .agent_id then .agent_id else "" end
    else
      ""
    end
  ' 2>/dev/null | head -n 1
}

call_litellm "GET" "$AGENTS_URL"
if ! is_success; then
  echo "Failed to list agents (${HTTP_CODE})"
  echo "${HTTP_BODY}"
  exit 1
fi

AGENT_ID=$(extract_agent_id "${HTTP_BODY}")

validate_agent_card() {
  local id="$1"
  if [ -z "$id" ]; then
    echo "No agent_id available for validation"
    return 1
  fi
  call_litellm "GET" "${A2A_BASE_URL}/a2a/${id}/.well-known/agent.json"
  if ! is_success; then
    echo "LiteLLM agent card fetch failed for ${AGENT_NAME} (${id}) (${HTTP_CODE})"
    echo "${HTTP_BODY}"
    return 1
  fi
  if ! echo "${HTTP_BODY}" | jq -e '.name and .description and .skills and .defaultInputModes and .defaultOutputModes' >/dev/null 2>&1; then
    echo "LiteLLM agent card validation failed for ${AGENT_NAME} (${id})"
    echo "${HTTP_BODY}"
    return 1
  fi
  echo "Validated LiteLLM agent card for ${AGENT_NAME} (${id})"
  return 0
}

if [ "$MODE" = "--validate-only" ]; then
  if [ -z "$AGENT_ID" ]; then
    echo "Could not find existing agent_id for ${AGENT_NAME}"
    echo "${HTTP_BODY}"
    exit 1
  fi
  validate_agent_card "$AGENT_ID"
  exit $?
fi

call_litellm "GET" "$AGENT_CARD_URL"
if ! is_success; then
  echo "Failed to fetch agent card from ${AGENT_CARD_URL} (${HTTP_CODE})"
  echo "${HTTP_BODY}"
  exit 1
fi
AGENT_CARD_JSON="${HTTP_BODY}"

AGENT_CONFIG_PAYLOAD=$(jq -cn \
  --arg agent_name "$AGENT_NAME" \
  --argjson card "$AGENT_CARD_JSON" \
  '{
    agent_name: $agent_name,
    agent_card_params: $card,
    litellm_params: {make_public: true}
  }')

AGENT_WRAPPED_PAYLOAD=$(jq -cn \
  --arg agent_name "$AGENT_NAME" \
  --argjson card "$AGENT_CARD_JSON" \
  '{
    agent: {
      agent_name: $agent_name,
      agent_card_params: $card,
      litellm_params: {make_public: true}
    }
  }')

REGISTER_BODY=""
call_litellm "POST" "$AGENTS_URL" "$AGENT_CONFIG_PAYLOAD"
if is_success; then
  REGISTER_BODY="$HTTP_BODY"
  AGENT_ID="$(extract_agent_id "$REGISTER_BODY")"
  echo "Registered ${AGENT_NAME} via ${AGENTS_URL}"
else
  echo "LiteLLM POST ${AGENTS_URL} failed (${HTTP_CODE})"
  echo "${HTTP_BODY}"

  call_litellm "POST" "$AGENTS_URL" "$AGENT_WRAPPED_PAYLOAD"
  if is_success; then
    REGISTER_BODY="$HTTP_BODY"
    AGENT_ID="$(extract_agent_id "$REGISTER_BODY")"
    echo "Registered ${AGENT_NAME} via wrapped payload at ${AGENTS_URL}"
  else
    echo "LiteLLM POST wrapped payload failed (${HTTP_CODE})"
    echo "${HTTP_BODY}"
    if [ -z "${AGENT_ID}" ]; then
      call_litellm "GET" "$AGENTS_URL"
      if is_success; then
        AGENT_ID="$(extract_agent_id "$HTTP_BODY")"
      fi
    fi
    if [ -z "${AGENT_ID}" ]; then
      echo "Could not find existing agent_id for ${AGENT_NAME}"
      exit 1
    fi
    call_litellm "PATCH" "${AGENTS_URL}/${AGENT_ID}" "$AGENT_CONFIG_PAYLOAD"
    if is_success; then
      echo "Patched existing agent ${AGENT_NAME} (${AGENT_ID})"
    else
      echo "LiteLLM PATCH ${AGENTS_URL}/${AGENT_ID} failed (${HTTP_CODE})"
      echo "${HTTP_BODY}"
      call_litellm "PUT" "${AGENTS_URL}/${AGENT_ID}" "$AGENT_CONFIG_PAYLOAD"
      if is_success; then
        echo "Updated existing agent ${AGENT_NAME} (${AGENT_ID})"
      else
        echo "LiteLLM PUT ${AGENTS_URL}/${AGENT_ID} failed (${HTTP_CODE})"
        echo "${HTTP_BODY}"
        exit 1
      fi
    fi
  fi
fi

if [ -z "${AGENT_ID}" ]; then
  call_litellm "GET" "$AGENTS_URL"
  if is_success; then
    AGENT_ID="$(extract_agent_id "$HTTP_BODY")"
  fi
fi

if [ -z "${AGENT_ID}" ]; then
  echo "Registration completed but no agent_id found for ${AGENT_NAME}"
  exit 1
fi

validate_agent_card "$AGENT_ID"
