"""Instructions: bundled paths, ownership rules, and the template format."""

from .documents import (
    BUNDLED_INSTRUCTION_GROUPS,
    RUNTIME_INSTRUCTION_GROUP,
    SYSTEM_INSTRUCTION,
    check_instruction_write,
    instruction_group,
    is_bundled_instruction,
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
    "BUNDLED_INSTRUCTION_GROUPS",
    "DEFAULT_DATA_DIR",
    "DEFAULT_INSTRUCTIONS_DIR",
    "PROJECT_ROOT",
    "RUNTIME_INSTRUCTION_GROUP",
    "SYSTEM_INSTRUCTION",
    "check_instruction_write",
    "instruction_group",
    "is_bundled_instruction",
    "is_runtime_instruction",
    "render_instruction",
    "resolve_data_dir",
    "resolve_instructions_dir",
]
