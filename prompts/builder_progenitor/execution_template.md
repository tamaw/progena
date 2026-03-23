Task metadata:
- Task name: {task_name}
- Work item id: {work_item_id}
- Branch name: {branch_name}
- Planned at (UTC): {planned_at_utc}
- Description: {description}
- Copilot goal: {copilot_goal}

Success criteria:
{success_criteria}

Validation targets:
{validation_targets}

Planner reasoning:
{reasoning}

User request:
{request_text}

Execution context (JSON):
{context_json}

Repository skills:
{skill_context}

Execution requirements:
- Create the requested worktree using the provided metadata.
- Use `render_copilot_prompt` before `copilot_build` so the ACP prompt is assembled from prompt files instead of inline text.
- Keep the work aligned with the task metadata above.
- Validate the work before committing.
- If `commit_changes` reports `committed=true`, you must call `create_pull_request`.
- Before finishing, check your own work against the task metadata, validation output, and PR result.
