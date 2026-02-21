"""Shared node type aliases to avoid circular imports."""

from collections.abc import Callable, Coroutine
from typing import Any

from ..state.models import ProgenitorState

NodeStateUpdate = dict[str, Any]
NodeFunction = Callable[[ProgenitorState], Coroutine[Any, Any, NodeStateUpdate]]
NodeFactory = Callable[..., NodeFunction]
