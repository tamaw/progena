---
name: Copilot Prompting
description: Build Copilot ACP prompts from structured task metadata before invoking copilot_build.
---

# Copilot Prompting Skill

Before calling `copilot_build`, assemble the ACP prompt with `render_copilot_prompt` so the prompt stays consistent with the structured task metadata and mounted prompt files.

Carry the same task name, work item id, branch name, description, and validation expectations from the planner output through execution.
