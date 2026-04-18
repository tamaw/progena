Task metadata:
- Task name: {task_name}
- Work item id: {work_item_id}
- Branch name: {branch_name}
- Planned at (UTC): {planned_at_utc}
- Description: {description}

Planner reasoning:
{reasoning}

User request:
{request_text}

Execution context (JSON):
{context_json}

Repository skills:
{skill_context}

Validation verdict (from the validate subagent):
{validation_summary}

Failure-mode teardown requirements:
- The validate subagent returned a `fail` verdict. Treat the build as not shippable.
- Do **not** push the branch.
- Do **not** call `create_pull_request`.
- Do **not** call `copilot_build` or `validate_code` in this phase.
- Discard any local commit by leaving the worktree untouched and call
  `remove_worktree` to clean up the workspace.
- End the response with a short failure summary that names the validation gaps
  exactly as listed in the validation report so the caller can act on them.
