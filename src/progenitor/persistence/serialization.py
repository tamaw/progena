"""Read historical checkpoint type paths after the application package rename.

Only constructor identities are translated. Message text, document paths and
binary values are preserved, and LangGraph still applies its deserialization
allowlist. New checkpoints use the normal src.* identities.
"""

from typing import Any

import ormsgpack
from langgraph.checkpoint.serde.jsonplus import (
    EXT_CONSTRUCTOR_KW_ARGS,
    EXT_CONSTRUCTOR_POS_ARGS,
    EXT_CONSTRUCTOR_SINGLE_ARG,
    EXT_DELTA_SNAPSHOT,
    EXT_METHOD_SINGLE_ARG,
    EXT_PYDANTIC_V1,
    EXT_PYDANTIC_V2,
    JsonPlusSerializer,
)

_OLD_PREFIX = "app.progenitor."
_CONSTRUCTOR_CODES = frozenset(
    {
        EXT_CONSTRUCTOR_SINGLE_ARG,
        EXT_CONSTRUCTOR_POS_ARGS,
        EXT_CONSTRUCTOR_KW_ARGS,
        EXT_METHOD_SINGLE_ARG,
        EXT_PYDANTIC_V1,
        EXT_PYDANTIC_V2,
    }
)


def _translate_extension(code: int, payload: bytes) -> ormsgpack.Ext:
    if _OLD_PREFIX.encode() not in payload or code not in _CONSTRUCTOR_CODES | {EXT_DELTA_SNAPSHOT}:
        return ormsgpack.Ext(code, payload)
    value = ormsgpack.unpackb(
        payload, ext_hook=_translate_extension, option=ormsgpack.OPT_NON_STR_KEYS
    )
    if (
        code in _CONSTRUCTOR_CODES
        and isinstance(value, list)
        and value
        and isinstance(value[0], str)
        and value[0].startswith(_OLD_PREFIX)
    ):
        value[0] = "src.progenitor." + value[0][len(_OLD_PREFIX) :]
    return ormsgpack.Ext(code, ormsgpack.packb(value, option=ormsgpack.OPT_NON_STR_KEYS))


class CheckpointSerializer(JsonPlusSerializer):
    """Stock LangGraph serialization with a read-only legacy module migration."""

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        kind, payload = data
        if kind == "msgpack" and _OLD_PREFIX.encode() in payload:
            value = ormsgpack.unpackb(
                payload, ext_hook=_translate_extension, option=ormsgpack.OPT_NON_STR_KEYS
            )
            data = kind, ormsgpack.packb(value, option=ormsgpack.OPT_NON_STR_KEYS)
        return super().loads_typed(data)
