"""
Utility functions for Progenitor.

Helpers for working with data structures.
"""

from typing import Any, TypeVar, cast

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
    return cast(T, value)


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
