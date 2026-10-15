---
title: Execution brief
description: Task metadata and goals for executing a planned build.
variables: [branch_name, context_json, copilot_goal, description, planned_at_utc, reasoning, request_text, skill_context, success_criteria, task_name, validation_targets, work_item_id]
---
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
- A task setup phase already ran in this thread. Reuse the prepared worktree instead of creating a new one.
- Use `render_copilot_prompt` before `copilot_build` so the ACP prompt is assembled from prompt files instead of inline text.
- Keep the work aligned with the task metadata above.
- Validate the work before committing.
- If the work is ready, create a commit; leave push, PR creation, and cleanup for the teardown phase.
- Before finishing, check your own work against the task metadata and validation output.
