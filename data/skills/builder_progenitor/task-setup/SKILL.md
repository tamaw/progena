---
name: task-setup
description: Prepare a fresh builder worktree from origin/main before the main build phase.
allowed-tools: git read_file
---

# Task Setup Skill

## Setup Sequence
1. Create a new task worktree from `origin/main`.
2. Confirm the task branch is active in that worktree.
3. Fetch the latest remote state if needed to verify the workspace is current.

## Tool Use
- Prefer `create_worktree` to establish the workspace and branch.
- Use git tools such as `git_fetch`, `git_status`, and `git_cli` to inspect or refresh the new worktree.
- Do not jump ahead to build, commit, PR, or cleanup actions during setup.

## Outcome
- Finish setup with a ready worktree path and branch that the execution phase can reuse.
