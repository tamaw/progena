---
name: github-agent-spawn
description: Dispatch GitHub-agent workflows from user requests and return run status for review
allowed-tools:
  - spawn_github_agent
  - list_tools
  - say_to_user
---

# GitHub Agent Spawn Skill

## When to Use
- User asks to spawn, launch, or dispatch a GitHub agent
- User wants a GitHub workflow run started from a chat instruction
- User asks for run status metadata after dispatch

## Preferred Flow
1. Confirm the user intent is to run a GitHub-agent workflow.
2. Call `spawn_github_agent` with the user request.
3. Return workflow/run metadata and any next review action for the user.

## Guard Rails
- Do not claim a workflow ran unless tool output confirms dispatch.
- Surface clear errors if GH auth/workflow inputs are missing.
- Keep deploy/merge decisions human-controlled.
