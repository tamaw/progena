"""Root conftest: ensure worktree packages and prompts resolve correctly before any imports."""

import os
from pathlib import Path

# Execute at module import time (when conftest.py is first loaded by pytest)
_root = Path(__file__).resolve().parent
_worktree_prompts = _root / "prompts"
if _worktree_prompts.exists():
    os.environ["PROGENITOR_PROMPTS_DIR"] = str(_worktree_prompts)


def pytest_load_initial_conftests(early_config, parser, args):
    """Override PROGENITOR_PROMPTS_DIR so observe_agent prompts resolve to worktree."""
    root = Path(__file__).resolve().parent
    worktree_prompts = root / "prompts"
    if worktree_prompts.exists():
        os.environ["PROGENITOR_PROMPTS_DIR"] = str(worktree_prompts)
