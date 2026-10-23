"""Instructions: runtime ownership rules and the template format."""

from .documents import (
    GENESIS_INSTRUCTION,
    RUNTIME_INSTRUCTION_GROUP,
    check_instruction_write,
    instruction_group,
    is_runtime_instruction,
    render_instruction,
    render_runtime_instruction,
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
    "check_instruction_write",
    "instruction_group",
    "is_runtime_instruction",
    "render_instruction",
    "render_runtime_instruction",
    "resolve_data_dir",
    "resolve_instructions_dir",
]
