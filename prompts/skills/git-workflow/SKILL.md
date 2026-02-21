---
name: git-workflow
description: Git operations including branching, committing, worktree management, PR creation, and merge conflict resolution
allowed-tools:
  - git
  - gh
  - shell
  - read_file
  - write_file
---

# Git Workflow Skill

## When to Use
- User asks to commit, branch, or push changes
- Code generation tasks need branch isolation
- PR creation or review is needed
- Merge conflicts need resolution

## Branching Strategy
- Feature branches: `feature/<name>` or `build/<work-item-id>`
- Build branches are created automatically by the build loop
- Always branch from the latest default branch

## Commit Best Practices
- Concise, descriptive commit messages
- One logical change per commit
- Include file context in message when helpful

## Worktree Isolation (Build Loop)
The build system uses git worktrees for parallel, isolated builds:
```
worktrees/<work-item-id>/    # Isolated checkout
├── .venv/                    # Isolated Python environment
└── (full repo copy)
```
- Each build gets its own worktree and venv
- Changes are committed in the worktree, then merged back
- Failed builds are automatically cleaned up

## PR Creation via GitHub CLI
```bash
gh pr create --title "..." --body "..." --base main --head feature/branch
```
Always include:
- Clear title describing the change
- Body with context and testing notes
- Appropriate labels if applicable

## Merge Conflict Resolution
1. Identify conflicting files
2. Read both versions
3. Use LLM-assisted merge when conflicts are complex
4. Verify merged result compiles/passes tests
