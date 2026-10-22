"""Instructions: runtime ownership rules and the template format."""

from .documents import (
    RUNTIME_INSTRUCTION_GROUP,
    SYSTEM_INSTRUCTION,
    check_instruction_write,
    instruction_group,
    is_runtime_instruction,
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
    "PROJECT_ROOT",
    "RUNTIME_INSTRUCTION_GROUP",
    "SYSTEM_INSTRUCTION",
    "check_instruction_write",
    "instruction_group",
    "is_runtime_instruction",
    "render_instruction",
    "resolve_data_dir",
    "resolve_instructions_dir",
]
