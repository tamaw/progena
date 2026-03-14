# check_task_status.sh

A shell script that queries the A2A and CI APIs for a given task and prints a concise JSON summary to stdout.

## Location

```
scripts/check_task_status.sh
```

## Requirements

| Requirement | Notes |
|---|---|
| `bash` ≥ 3.2 | Standard on macOS/Linux |
| `curl` | Must be on `PATH` |
| `jq` (optional) | Strongly recommended. Without it, raw JSON is emitted for `tests_summary` / `lint_summary`. |

## Required Environment Variables

| Variable | Description |
|---|---|
| `A2A_TOKEN` | Bearer token used in the `Authorization` header for all API calls. |

## Optional Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TASK_ID` | _(argument $1)_ | Task ID to query. May also be passed as the first positional argument. |
| `A2A_BASE` | `https://a2a.service` | Base URL for the A2A API. |
| `CI_BASE` | `https://ci.example.com` | Base URL for the CI API. |

## Usage

```bash
# Pass task ID as an argument
A2A_TOKEN=your-token ./scripts/check_task_status.sh <task-id>

# Or set it as an env var
export A2A_TOKEN=your-token
export TASK_ID=abc-123
./scripts/check_task_status.sh

# Override API base URLs (e.g. for local testing)
A2A_TOKEN=token A2A_BASE=http://localhost:8080 CI_BASE=http://localhost:9090 \
  ./scripts/check_task_status.sh abc-123
```

## Output

The script writes a single JSON object to **stdout**:

```json
{
  "task_id": "abc-123",
  "repo": "owner/repo",
  "branch": "feature/my-branch",
  "build_id": "build-456",
  "build_status": "success",
  "tests_summary": {
    "total": 120,
    "passed": 118,
    "failed": 2,
    "skipped": 0
  },
  "lint_summary": {
    "status": "passed",
    "violations": 0
  },
  "pr_url": "https://github.com/owner/repo/pull/42",
  "errors": []
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `task_id` | string | The queried task ID. |
| `repo` | string \| null | Repository in `owner/repo` format, or `null` if not found. |
| `branch` | string \| null | Branch name, or `null` if not found. |
| `build_id` | string \| null | CI build identifier, or `null` if not available. |
| `build_status` | string | Build state (e.g. `success`, `failed`, `running`, `unknown`). |
| `tests_summary` | object \| null | Aggregated test counts from CI; `null` if build_id absent or endpoint unreachable. |
| `lint_summary` | object \| null | Lint status and violation count from CI; `null` if build_id absent or endpoint unreachable. |
| `pr_url` | string \| null | Pull-request URL if present in the task record. |
| `errors` | array | Non-fatal warnings / errors encountered during execution (e.g. unreachable endpoints). |

Warnings and non-fatal errors are also printed to **stderr** as they occur.

## API Queries Performed

1. **Task record** — `GET {A2A_BASE}/api/tasks/{task-id}`
2. **Context search** _(fallback when repo/build_id missing)_ — `GET {A2A_BASE}/api/tasks?context={context_id}`
3. **CI build** _(when build_id known)_ — `GET {CI_BASE}/api/builds/{build-id}`
4. **CI tests** _(when build_id known)_ — `GET {CI_BASE}/api/builds/{build-id}/tests`
5. **CI lint** _(when build_id known)_ — `GET {CI_BASE}/api/builds/{build-id}/lint`

All requests include `Authorization: Bearer $A2A_TOKEN` and `Accept: application/json` headers.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Script ran to completion (check `errors` array for soft failures). |
| `1` | Fatal error (missing `TASK_ID`, missing `A2A_TOKEN`, `curl` not found). |
