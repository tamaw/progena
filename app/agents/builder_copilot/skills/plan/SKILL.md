---
name: plan
description: Plan the requested change before editing. Use when starting a new builder-copilot task so the worktree, scope, and validation steps are explicit.
allowed-tools: summarize_request create_worktree python_search run_worktree_command
---

# Plan

- Normalize the request first.
- Create a worktree unless one is already provided.
- Inspect the target code before making changes.
- State the exact build, test, lint, and PR path you intend to follow.
