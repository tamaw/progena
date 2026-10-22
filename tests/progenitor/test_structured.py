"""Tests for the shared structured-output engine (src.progenitor.llm.structured)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import src.progenitor.llm.structured as structured

# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def test_pure_helpers() -> None:
    assert structured.schema_safe_name("my-doc.name") == "my_doc_name"
    assert structured.schema_safe_name("!!!") == "structured_output"

    wrapped = structured.wrap_array_schema({"type": "array", "items": {"type": "string"}})
    assert wrapped["type"] == "object"
    assert wrapped["properties"]["items"]["type"] == "array"
    assert wrapped["required"] == ["items"]

    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    assert structured.validate_against_schema({"n": 1}, schema) is None
    assert structured.validate_against_schema({"n": "x"}, schema) is not None


# --------------------------------------------------------------------------- #
# generate_structured
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_structured_object_roundtrip() -> None:
    llm = MagicMock()
    llm.chat_structured_json = AsyncMock(return_value={"n": 5})
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}

    value, error = await structured.generate_structured(
        llm, json_schema=schema, instruction="produce n=5"
    )

    assert error is None
    assert value == {"n": 5}
    _, kwargs = llm.chat_structured_json.call_args
    assert kwargs["strict"] is False
    assert kwargs["json_schema"] == schema  # object schema passed through unwrapped


@pytest.mark.asyncio
async def test_generate_structured_array_wraps_and_unwraps() -> None:
    llm = MagicMock()
    llm.chat_structured_json = AsyncMock(return_value={"items": [1, 2, 3]})
    schema = {"type": "array", "items": {"type": "integer"}}

    value, error = await structured.generate_structured(
        llm,
        json_schema=schema,
        instruction="list 1..3",
        context=[1],
    )

    assert error is None
    assert value == [1, 2, 3]
    _, kwargs = llm.chat_structured_json.call_args
    assert kwargs["json_schema"]["type"] == "object"
    assert "items" in kwargs["json_schema"]["properties"]
    # The array context is wrapped too.
    assert '"items"' in kwargs["prompt"]


@pytest.mark.asyncio
async def test_generate_structured_array_schema_keeps_string_context() -> None:
    """A non-list context (e.g. source text to extract from) must not be dropped."""
    llm = MagicMock()
    llm.chat_structured_json = AsyncMock(return_value={"items": ["a", "b"]})
    schema = {"type": "array", "items": {"type": "string"}}

    value, error = await structured.generate_structured(
        llm,
        json_schema=schema,
        instruction="extract the tags",
        context="please tag this as a and b",
    )

    assert error is None
    assert value == ["a", "b"]
    _, kwargs = llm.chat_structured_json.call_args
    assert "please tag this as a and b" in kwargs["prompt"]


@pytest.mark.asyncio
async def test_generate_structured_validation_error() -> None:
    llm = MagicMock()
    llm.chat_structured_json = AsyncMock(return_value={"n": "not-an-int"})
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}

    value, error = await structured.generate_structured(
        llm, json_schema=schema, instruction="produce n"
    )

    assert value is None
    assert error is not None and "does not conform" in error


@pytest.mark.asyncio
async def test_generate_structured_missing_array_wrapper() -> None:
    llm = MagicMock()
    llm.chat_structured_json = AsyncMock(return_value=[1, 2, 3])  # not wrapped
    schema = {"type": "array", "items": {"type": "integer"}}

    value, error = await structured.generate_structured(llm, json_schema=schema, instruction="list")

    assert value is None
    assert error is not None and "wrapper" in error
