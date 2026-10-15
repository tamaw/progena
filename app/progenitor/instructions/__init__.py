"""Instruction documents: bundled paths and the shared front matter rules."""

from .documents import (
    GENESIS_INSTRUCTION,
    RUNTIME_INSTRUCTION_GROUP,
    instruction_group,
    is_runtime_instruction,
    parse_instruction,
    render_instruction,
)
from .paths import (
    DEFAULT_DATA_DIR,
    DEFAULT_INSTRUCTIONS_DIR,
    PROJECT_ROOT,
    resolve_data_dir,
    resolve_instructions_dir,
)

__all__ = [
    "DEFAULT_DATA_DIR",
    "DEFAULT_INSTRUCTIONS_DIR",
    "GENESIS_INSTRUCTION",
    "PROJECT_ROOT",
    "RUNTIME_INSTRUCTION_GROUP",
    "instruction_group",
    "is_runtime_instruction",
    "parse_instruction",
    "render_instruction",
    "resolve_data_dir",
    "resolve_instructions_dir",
]
