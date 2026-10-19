"""Unit tests for the pure Responses API conversion helpers."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.progenitor.runtime.responses_api import (
    build_response_object,
    build_sse_event,
    conversation_items_with_sources,
    error_response,
    messages_to_conversation_items,
    paginate_items,
    with_langchain_messages,
)


class TestErrorResponse:
    def test_returns_tuple(self) -> None:
        payload, status = error_response("bad", "bad_request", 400)
        assert status == 400
        assert payload["error"]["message"] == "bad"
        assert payload["error"]["code"] == "bad_request"
        assert payload["error"]["type"] == "invalid_request_error"
        assert payload["error"]["param"] is None


class TestMessagesToConversationItems:
    def test_human_string_content(self) -> None:
        msg = HumanMessage(content="hello", id="h1")
        items = messages_to_conversation_items([msg], "conv1")
        assert len(items) == 1
        assert items[0]["type"] == "message"
        assert items[0]["role"] == "user"
        assert items[0]["content"] == [{"type": "input_text", "text": "hello"}]

    def test_human_list_content(self) -> None:
        msg = HumanMessage(
            content=[{"type": "input_text", "text": "a"}, {"type": "input_text", "text": "b"}],
            id="h2",
        )
        items = messages_to_conversation_items([msg], "conv1")
        assert items[0]["content"] == [
            {"type": "input_text", "text": "a"},
            {"type": "input_text", "text": "b"},
        ]

    def test_human_multimodal_content_maps_to_input_parts(self) -> None:
        url = "data:image/jpeg;base64,QUJD"
        msg = HumanMessage(
            content=[
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": url}},
            ],
            id="h3",
        )
        items = messages_to_conversation_items([msg], "conv1")
        assert items[0]["content"] == [
            {"type": "input_text", "text": "look"},
            {"type": "input_image", "image_url": url, "detail": "auto"},
        ]

    def test_system_message(self) -> None:
        msg = SystemMessage(content="sys", id="s1")
        items = messages_to_conversation_items([msg], "conv1")
        assert items[0]["role"] == "system"

    def test_ai_string_content(self) -> None:
        msg = AIMessage(content="hi", id="a1")
        items = messages_to_conversation_items([msg], "conv1")
        assert len(items) == 1
        assert items[0]["type"] == "message"
        assert items[0]["role"] == "assistant"
        assert items[0]["content"] == [{"type": "output_text", "text": "hi", "annotations": []}]

    def test_ai_responses_v1_blocks(self) -> None:
        msg = AIMessage(
            content=[
                {"type": "text", "text": "hello", "id": "t1"},
                {
                    "type": "reasoning",
                    "id": "r1",
                    "summary": [{"type": "summary_text", "text": "think"}],
                },
                {"type": "text", "text": "world", "id": "t2"},
                {"type": "function_call", "call_id": "c1", "name": "foo", "arguments": '{"x": 1}'},
            ],
            id="a2",
        )
        items = messages_to_conversation_items([msg], "conv1")
        assert len(items) == 4

        # first text block (not consecutive with third because reasoning sits between)
        assert items[0]["type"] == "message"
        assert items[0]["content"] == [
            {"type": "output_text", "text": "hello", "annotations": []},
        ]
        assert items[0]["id"] == "t1"

        # reasoning
        assert items[1]["type"] == "reasoning"
        assert items[1]["id"] == "r1"
        assert items[1]["summary"] == [{"type": "summary_text", "text": "think"}]

        # second text block
        assert items[2]["type"] == "message"
        assert items[2]["content"] == [
            {"type": "output_text", "text": "world", "annotations": []},
        ]

        # function_call
        assert items[3]["type"] == "function_call"
        assert items[3]["call_id"] == "c1"
        assert items[3]["name"] == "foo"
        assert items[3]["arguments"] == '{"x": 1}'

    def test_ai_passthrough_block(self) -> None:
        msg = AIMessage(content=[{"type": "custom", "id": "c1", "value": 42}], id="a3")
        items = messages_to_conversation_items([msg], "conv1")
        assert len(items) == 1
        assert items[0]["type"] == "custom"
        assert items[0]["value"] == 42
        assert items[0]["id"] == "c1"

    def test_ai_tool_calls_fallback(self) -> None:
        msg = AIMessage(
            content=[],
            id="a4",
            tool_calls=[{"id": "tc1", "name": "bar", "args": {"y": 2}}],
        )
        items = messages_to_conversation_items([msg], "conv1")
        assert len(items) == 1
        assert items[0]["type"] == "function_call"
        assert items[0]["call_id"] == "tc1"
        assert items[0]["arguments"] == '{"y": 2}'

    def test_ai_empty_message(self) -> None:
        msg = AIMessage(content=[], id="a5")
        items = messages_to_conversation_items([msg], "conv1")
        assert len(items) == 1
        assert items[0]["type"] == "message"
        assert items[0]["role"] == "assistant"
        assert items[0]["content"] == []

    def test_tool_message(self) -> None:
        msg = ToolMessage(content="result", tool_call_id="c1", id="t1")
        items = messages_to_conversation_items([msg], "conv1")
        assert len(items) == 1
        assert items[0]["type"] == "function_call_output"
        assert items[0]["call_id"] == "c1"
        assert items[0]["output"] == "result"

    def test_tool_message_block_content_becomes_input_parts(self) -> None:
        msg = ToolMessage(
            content=[
                "plain",
                {"type": "text", "text": "ok"},
                {"type": "image", "base64": "QUJD", "mime_type": "image/jpeg"},
                {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
                {"type": "custom", "value": 1},
            ],
            tool_call_id="c2",
            id="t2",
        )
        [item] = messages_to_conversation_items([msg], "conv1")
        assert item["output"] == [
            {"type": "input_text", "text": "plain"},
            {"type": "input_text", "text": "ok"},
            {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD", "detail": "auto"},
            {"type": "input_image", "image_url": "https://x/y.png", "detail": "auto"},
            {"type": "input_text", "text": '{"type": "custom", "value": 1}'},
        ]

    def test_id_collisions(self) -> None:
        msg1 = HumanMessage(content="a", id="same")
        msg2 = HumanMessage(content="b", id="same")
        items = messages_to_conversation_items([msg1, msg2], "conv1")
        assert items[0]["id"] == "same"
        assert items[1]["id"] == "same:1"

    def test_empty_string_content_with_tool_calls_maps_function_calls(self) -> None:
        msg = AIMessage(
            content="", id="a1", tool_calls=[{"id": "c1", "name": "f", "args": {"x": 1}}]
        )
        items = messages_to_conversation_items([msg], "conv1")
        assert [(item["type"], item["call_id"], item["arguments"]) for item in items] == [
            ("function_call", "c1", '{"x": 1}')
        ]

    def test_missing_ids_use_conversation_fallback(self) -> None:
        items = messages_to_conversation_items([HumanMessage(content="x")], "conv1")
        assert items[0]["id"] == "conv1:0"


class TestLangchainMessageInclude:
    def _annotated_page(self, messages: list[Any], start: int, limit: int) -> list[dict[str, Any]]:
        sourced = conversation_items_with_sources(messages, "conv1")
        after = sourced[start - 1][1]["id"] if start else None
        page, _ = paginate_items(
            [item for _, item in sourced], after=after, limit=limit, order="asc"
        )
        return with_langchain_messages(
            page, {item["id"]: index for index, item in sourced}, messages, "conv1"
        )

    def test_first_item_of_each_message_in_page_carries_message(self) -> None:
        messages = [
            HumanMessage(content="hi", id="h1"),
            AIMessage(
                content=[
                    {"type": "reasoning", "id": "r1", "summary": []},
                    {"type": "text", "id": "m1", "text": "a"},
                ],
                id="a1",
            ),
        ]
        page = self._annotated_page(messages, start=0, limit=10)
        assert [(item["source_message_id"], "langchain_message" in item) for item in page] == [
            ("h1", True),
            ("a1", True),
            ("a1", False),
        ]
        assert page[1]["langchain_message"]["type"] == "ai"

    def test_page_starting_mid_message_still_carries_that_message(self) -> None:
        messages = [
            AIMessage(
                content=[
                    {"type": "reasoning", "id": "r1", "summary": []},
                    {"type": "text", "id": "m1", "text": "a"},
                ],
                id="a1",
            )
        ]
        page = self._annotated_page(messages, start=1, limit=10)
        assert [item["id"] for item in page] == ["m1"]
        assert page[0]["langchain_message"]["data"]["id"] == "a1"


class TestPaginateItems:
    def _make_items(self, n: int) -> list[dict[str, Any]]:
        return [{"id": f"item-{i}"} for i in range(n)]

    def test_asc_basic(self) -> None:
        page, meta = paginate_items(self._make_items(5), after=None, limit=2, order="asc")
        assert [i["id"] for i in page] == ["item-0", "item-1"]
        assert meta["first_id"] == "item-0"
        assert meta["last_id"] == "item-1"
        assert meta["has_more"] is True

    def test_desc_basic(self) -> None:
        page, meta = paginate_items(self._make_items(5), after=None, limit=2, order="desc")
        assert [i["id"] for i in page] == ["item-4", "item-3"]

    def test_after_exclusive(self) -> None:
        page, meta = paginate_items(self._make_items(5), after="item-1", limit=2, order="asc")
        assert [i["id"] for i in page] == ["item-2", "item-3"]

    def test_unknown_after_raises(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            paginate_items(self._make_items(3), after="nope", limit=1, order="asc")

    def test_empty(self) -> None:
        page, meta = paginate_items([], after=None, limit=10, order="asc")
        assert page == []
        assert meta["first_id"] is None
        assert meta["last_id"] is None
        assert meta["has_more"] is False


class TestBuildResponseObject:
    def test_shape(self) -> None:
        obj = build_response_object(
            "r1", "t1", "m1", "completed", [{"type": "message"}], {"k": "v"}
        )
        assert obj["id"] == "r1"
        assert obj["object"] == "response"
        assert obj["status"] == "completed"
        assert obj["conversation"] == {"id": "t1"}
        assert obj["output"] == [{"type": "message"}]
        assert obj["metadata"] == {"k": "v"}
        assert obj["error"] is None
        assert isinstance(obj["created_at"], int)


class TestBuildSseEvent:
    def test_sequence_number(self) -> None:
        ev = build_sse_event("x", {"a": 1}, 5)
        assert ev["type"] == "x"
        assert ev["sequence_number"] == 5
        assert ev["a"] == 1
