"""
Common utility functions.

Shared helpers for working with data structures across the codebase.
"""

from typing import Any, TypeVar, Optional

T = TypeVar("T")


def get_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Get attribute from object or dict uniformly.

    Works with both:
    - Objects with attributes (e.g., Pydantic models, dataclasses)
    - Dictionaries

    Args:
        obj: The object or dict to access
        name: The attribute/key name
        default: Default value if not found

    Returns:
        The attribute value or default

    Example:
        >>> get_attr({"action": "build"}, "action")
        'build'
        >>> get_attr(decision_obj, "action")
        'build'
    """
    if obj is None:
        return default

    if isinstance(obj, dict):
        return obj.get(name, default)

    return getattr(obj, name, default)


def get_attr_or_default(obj: Any, name: str, default: T) -> T:
    """Type-safe version of get_attr with guaranteed return type.

    Args:
        obj: The object or dict to access
        name: The attribute/key name
        default: Default value (determines return type)

    Returns:
        The attribute value cast to type of default, or default
    """
    value = get_attr(obj, name, default)
    if value is None:
        return default
    return value


def get_enum_value(obj: Any, default: str = "") -> str:
    """Extract string value from an enum or return string as-is.

    Args:
        obj: An enum instance or string
        default: Default if obj is None

    Returns:
        The string value
    """
    if obj is None:
        return default
    if hasattr(obj, "value"):
        return str(obj.value)
    return str(obj)


def is_truthy(value: Any) -> bool:
    """Check if a value is truthy in a None-safe way.

    Handles common edge cases:
    - None -> False
    - Empty string -> False
    - Empty list/dict -> False
    - 0 -> False
    - False -> False
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return bool(value)
