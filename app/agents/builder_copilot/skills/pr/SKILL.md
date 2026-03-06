---
name: pr
description: Commit validated work and open a pull request. Use only after the build, test, and lint steps are complete or explicitly waived.
allowed-tools: commit_changes create_pull_request run_worktree_command
---

# PR

- Confirm there are changes to commit.
- Use a clear, descriptive commit message.
- After committing, use `gh pr create --title "<title>" --body "<description>"` to open the PR.
- Always specify `--base main` to target the main branch.
- Include test notes and validation results in the PR body.
- Wait for the command to complete and extract the PR URL from the output.
- Return the PR URL as part of the final summary.
