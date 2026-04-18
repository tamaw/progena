---
name: task-setup
description: Prepare a builder worktree deliberately before execution by creating or reusing a worktree, syncing it safely, and stopping on repo-state blockers.
allowed-tools: git read_file
---

# Task Setup Skill

## When to Use
- A build task is about to start
- The builder needs to create or reuse a worktree
- Repository readiness must be checked before editing

## Setup Workflow
1. Create or identify the worktree for the task.
2. Call `prepare_worktree` on the chosen worktree.
3. Read the structured result before continuing.

## Readiness Rules
- If `ready=true`, continue with execution.
- If `reason=local_changes`, do not proceed blindly; report the dirty worktree or choose a fresh one.
- If `reason=non_ff`, stop and surface that the branch needs manual merge/rebase handling.
- If `reason=no_upstream`, the worktree can still be usable for a new task branch.
- Do not hide setup blockers behind generic success text.

## Phase Discipline
- The setup phase is not the build phase.
- Do not call Copilot build, commit, or PR tools until worktree readiness is established.
