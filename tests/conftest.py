"""Root pytest configuration.

A developer's local ``.env`` may point the ``PROGENITOR_*_DIR`` variables at
another checkout (a long-running ``main`` worktree). ``load_dotenv`` never
overrides variables that are already set, so pinning them here, before any
``app`` module is imported, makes the shipped schemas and path defaults under
test the ones in this checkout -- the same ones CI sees.
"""

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

os.environ.update(
    PROGENITOR_DATA_DIR=str(_ROOT / "data"),
    PROGENITOR_STATE_DIR=str(_ROOT / "data" / "state"),
    PROGENITOR_LOG_DIR=str(_ROOT / "logs"),
    PROGENITOR_WORKTREE_DIR=str(_ROOT / "worktrees"),
    PROGENITOR_PROMPTS_DIR=str(_ROOT / "data" / "prompts"),
)
