#!/usr/bin/env bash
# check_task_status.sh - Query A2A and CI APIs for a task's status and output a JSON summary.
#
# Usage:
#   ./scripts/check_task_status.sh <task-id>
#   TASK_ID=<task-id> ./scripts/check_task_status.sh
#
# Required env vars:
#   A2A_TOKEN   - Bearer token for A2A API authorization
#
# Optional env vars:
#   TASK_ID     - Task ID (can also be supplied as $1)
#   A2A_BASE    - A2A API base URL (default: https://a2a.service)
#   CI_BASE     - CI API base URL   (default: https://ci.example.com)

set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command '$1' not found. Please install it."
}

require_cmd curl

HAS_JQ=false
command -v jq >/dev/null 2>&1 && HAS_JQ=true

TASK_ID="${1:-${TASK_ID:-}}"
[[ -n "$TASK_ID" ]] || die "Task ID required. Pass as argument or set TASK_ID env var."

A2A_TOKEN="${A2A_TOKEN:-}"
[[ -n "$A2A_TOKEN" ]] || die "A2A_TOKEN env var is required."

A2A_BASE="${A2A_BASE:-https://a2a.service}"
CI_BASE="${CI_BASE:-https://ci.example.com}"

safe_curl() {
    local url="$1"
    shift
    curl --silent --show-error --fail \
        --max-time 30 \
        "$@" "$url" 2>/dev/null || true
}

auth_header() {
    echo "Authorization: Bearer ${A2A_TOKEN}"
}

json_escape() {
    local value="$1"
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//$'\n'/\\n}
    value=${value//$'\r'/\\r}
    value=${value//$'\t'/\\t}
    printf '%s' "$value"
}

jq_get() {
    # jq_get <field> <json_string>
    local field="$1"
    local json="$2"

    if $HAS_JQ; then
        echo "$json" | jq -r "$field // empty" 2>/dev/null || true
        return
    fi

    local key="${field#.}"
    echo "$json" | sed -nE "s/.*\"${key}\"[[:space:]]*:[[:space:]]*\"([^\"]*)\".*/\\1/p" | head -1
}

ERROR_MESSAGES=()

append_error() {
    local msg="$1"
    warn "$msg"
    ERROR_MESSAGES+=("$msg")
}

errors_json() {
    if $HAS_JQ; then
        printf '%s\0' "${ERROR_MESSAGES[@]}" | jq -Rs 'split("\u0000")[:-1]'
        return
    fi

    local first=true
    printf '['
    for msg in "${ERROR_MESSAGES[@]}"; do
        if $first; then
            first=false
        else
            printf ', '
        fi
        printf '"%s"' "$(json_escape "$msg")"
    done
    printf ']'
}

TASK_RECORD=$(safe_curl "${A2A_BASE}/api/tasks/${TASK_ID}" \
    -H "$(auth_header)" \
    -H "Accept: application/json")

if [[ -z "$TASK_RECORD" ]]; then
    append_error "No response from A2A task endpoint for task ${TASK_ID}"
    TASK_RECORD="{}"
fi

REPO=""
BRANCH=""
BUILD_ID=""
PR_URL=""
A2A_CONTEXT_ID=""

if $HAS_JQ; then
    REPO=$(echo "$TASK_RECORD" | jq -r '.repo // .repository // empty' 2>/dev/null || true)
    BRANCH=$(echo "$TASK_RECORD" | jq -r '.branch // empty' 2>/dev/null || true)
    BUILD_ID=$(echo "$TASK_RECORD" | jq -r '.build_id // .buildId // empty' 2>/dev/null || true)
    PR_URL=$(echo "$TASK_RECORD" | jq -r '.pr_url // .prUrl // empty' 2>/dev/null || true)
    A2A_CONTEXT_ID=$(echo "$TASK_RECORD" | jq -r '.context_id // .contextId // empty' 2>/dev/null || true)
else
    REPO=$(jq_get ".repo" "$TASK_RECORD")
    [[ -z "$REPO" ]] && REPO=$(jq_get ".repository" "$TASK_RECORD")
    BRANCH=$(jq_get ".branch" "$TASK_RECORD")
    BUILD_ID=$(jq_get ".build_id" "$TASK_RECORD")
    [[ -z "$BUILD_ID" ]] && BUILD_ID=$(jq_get ".buildId" "$TASK_RECORD")
    PR_URL=$(jq_get ".pr_url" "$TASK_RECORD")
    [[ -z "$PR_URL" ]] && PR_URL=$(jq_get ".prUrl" "$TASK_RECORD")
    A2A_CONTEXT_ID=$(jq_get ".context_id" "$TASK_RECORD")
    [[ -z "$A2A_CONTEXT_ID" ]] && A2A_CONTEXT_ID=$(jq_get ".contextId" "$TASK_RECORD")
fi

if [[ -z "$REPO" ]] || [[ -z "$BUILD_ID" ]]; then
    if [[ -n "$A2A_CONTEXT_ID" ]]; then
        CONTEXT_RESP=$(safe_curl "${A2A_BASE}/api/tasks?context=${A2A_CONTEXT_ID}" \
            -H "$(auth_header)" \
            -H "Accept: application/json")

        if [[ -n "$CONTEXT_RESP" ]]; then
            if $HAS_JQ; then
                MATCHED=$(echo "$CONTEXT_RESP" | jq -c --arg id "$TASK_ID" '
                    if type == "array" then
                        first(.[] | select((.id // .task_id) == $id))
                    elif type == "object" and (.tasks | type? == "array") then
                        first(.tasks[] | select((.id // .task_id) == $id))
                    else
                        empty
                    end
                ' 2>/dev/null || true)
            else
                MATCHED="$CONTEXT_RESP"
            fi

            if [[ -n "${MATCHED:-}" ]] && [[ "$MATCHED" != "null" ]]; then
                if $HAS_JQ; then
                    [[ -z "$REPO" ]] && REPO=$(echo "$MATCHED" | jq -r '.repo // .repository // empty' 2>/dev/null || true)
                    [[ -z "$BRANCH" ]] && BRANCH=$(echo "$MATCHED" | jq -r '.branch // empty' 2>/dev/null || true)
                    [[ -z "$BUILD_ID" ]] && BUILD_ID=$(echo "$MATCHED" | jq -r '.build_id // .buildId // empty' 2>/dev/null || true)
                    [[ -z "$PR_URL" ]] && PR_URL=$(echo "$MATCHED" | jq -r '.pr_url // .prUrl // empty' 2>/dev/null || true)
                else
                    [[ -z "$REPO" ]] && REPO=$(jq_get ".repo" "$MATCHED")
                    [[ -z "$BRANCH" ]] && BRANCH=$(jq_get ".branch" "$MATCHED")
                    [[ -z "$BUILD_ID" ]] && BUILD_ID=$(jq_get ".build_id" "$MATCHED")
                    [[ -z "$PR_URL" ]] && PR_URL=$(jq_get ".pr_url" "$MATCHED")
                fi
            fi
        else
            append_error "No response from A2A context search for context ${A2A_CONTEXT_ID}"
        fi
    fi
fi

REPO="${REPO:-}"
BRANCH="${BRANCH:-}"
BUILD_ID="${BUILD_ID:-}"
PR_URL="${PR_URL:-}"

BUILD_STATUS="unknown"
TESTS_SUMMARY="null"
LINT_SUMMARY="null"

if [[ -n "$BUILD_ID" ]]; then
    BUILD_RESP=$(safe_curl "${CI_BASE}/api/builds/${BUILD_ID}" \
        -H "$(auth_header)" \
        -H "Accept: application/json")

    if [[ -n "$BUILD_RESP" ]]; then
        if $HAS_JQ; then
            BUILD_STATUS=$(echo "$BUILD_RESP" | jq -r '.status // .state // "unknown"' 2>/dev/null || echo "unknown")
        else
            BUILD_STATUS=$(jq_get ".status" "$BUILD_RESP")
            [[ -z "$BUILD_STATUS" ]] && BUILD_STATUS=$(jq_get ".state" "$BUILD_RESP")
            [[ -z "$BUILD_STATUS" ]] && BUILD_STATUS="unknown"
        fi
    else
        append_error "No response from CI builds endpoint for build ${BUILD_ID}"
    fi

    TESTS_RESP=$(safe_curl "${CI_BASE}/api/builds/${BUILD_ID}/tests" \
        -H "$(auth_header)" \
        -H "Accept: application/json")

    if [[ -n "$TESTS_RESP" ]]; then
        if $HAS_JQ; then
            TESTS_SUMMARY=$(echo "$TESTS_RESP" | jq -c '{total: (.total // .count // null), passed: (.passed // null), failed: (.failed // null), skipped: (.skipped // null)}' 2>/dev/null || echo "null")
        else
            TESTS_SUMMARY="$TESTS_RESP"
        fi
    else
        append_error "No response from CI tests endpoint for build ${BUILD_ID}"
    fi

    LINT_RESP=$(safe_curl "${CI_BASE}/api/builds/${BUILD_ID}/lint" \
        -H "$(auth_header)" \
        -H "Accept: application/json")

    if [[ -n "$LINT_RESP" ]]; then
        if $HAS_JQ; then
            LINT_SUMMARY=$(echo "$LINT_RESP" | jq -c '{status: (.status // null), violations: (.violations // .issues // null)}' 2>/dev/null || echo "null")
        else
            LINT_SUMMARY="$LINT_RESP"
        fi
    else
        append_error "No response from CI lint endpoint for build ${BUILD_ID}"
    fi
fi

ERRORS_JSON=$(errors_json)

if $HAS_JQ; then
    jq -cn \
        --arg task_id "$TASK_ID" \
        --arg repo "$REPO" \
        --arg branch "$BRANCH" \
        --arg build_id "$BUILD_ID" \
        --arg build_status "$BUILD_STATUS" \
        --argjson tests_summary "${TESTS_SUMMARY:-null}" \
        --argjson lint_summary "${LINT_SUMMARY:-null}" \
        --arg pr_url "$PR_URL" \
        --argjson errors "$ERRORS_JSON" \
        '{
            task_id: $task_id,
            repo: (if $repo == "" then null else $repo end),
            branch: (if $branch == "" then null else $branch end),
            build_id: (if $build_id == "" then null else $build_id end),
            build_status: $build_status,
            tests_summary: $tests_summary,
            lint_summary: $lint_summary,
            pr_url: (if $pr_url == "" then null else $pr_url end),
            errors: $errors
        }'
else
    _q() {
        if [[ -n "$1" ]]; then
            printf '"%s"' "$(json_escape "$1")"
        else
            printf 'null'
        fi
    }

    printf '{\n'
    printf '  "task_id": %s,\n' "$(_q "$TASK_ID")"
    printf '  "repo": %s,\n' "$(_q "$REPO")"
    printf '  "branch": %s,\n' "$(_q "$BRANCH")"
    printf '  "build_id": %s,\n' "$(_q "$BUILD_ID")"
    printf '  "build_status": %s,\n' "$(_q "$BUILD_STATUS")"
    printf '  "tests_summary": %s,\n' "${TESTS_SUMMARY:-null}"
    printf '  "lint_summary": %s,\n' "${LINT_SUMMARY:-null}"
    printf '  "pr_url": %s,\n' "$(_q "$PR_URL")"
    printf '  "errors": %s\n' "$ERRORS_JSON"
    printf '}\n'
fi
