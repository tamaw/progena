"""Tests for LLM client helpers and message handling."""

from app.progenitor.llm.client import _build_responses_endpoint
from app.progenitor.llm.messages import extract_text_and_reasoning


def _extract(data: dict):
    return extract_text_and_reasoning(data)


def test_build_responses_endpoint_from_base_url():
    endpoint = _build_responses_endpoint("https://example.openai.azure.com/openai/v1")
    assert endpoint == "https://example.openai.azure.com/openai/v1/responses"


def test_build_responses_endpoint_keeps_existing_responses_path_and_query():
    endpoint = _build_responses_endpoint(
        "https://example.openai.azure.com/openai/v1/responses?api-version=preview"
    )
    assert endpoint == "https://example.openai.azure.com/openai/v1/responses?api-version=preview"


def test_extracts_top_level_text_string():
    text, reasoning = _extract({"text": "Hello from top-level text"})
    assert text == "Hello from top-level text"
    assert reasoning == ""


def test_extracts_top_level_text_dict():
    text, reasoning = _extract({"text": {"text": "Hello from dict text"}})
    assert text == "Hello from dict text"
    assert reasoning == ""


def test_extracts_top_level_text_list_parts():
    text, reasoning = _extract({"text": ["Hello ", {"text": "world"}]})
    assert text == "Hello world"
    assert reasoning == ""


def test_extracts_top_level_text_nested_value_list():
    payload = {
        "text": {
            "value": [
                {"type": "output_text", "text": "Hello "},
                {"type": "output_text", "text": {"value": "nested"}},
            ]
        }
    }
    text, reasoning = _extract(payload)
    assert text == "Hello nested"
    assert reasoning == ""


def test_extracts_output_message_part_with_nested_text_dict():
    payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": {"value": "From output content"}}],
            }
        ]
    }
    text, reasoning = _extract(payload)
    assert text == "From output content"
    assert reasoning == ""


def test_extract_strips_inline_tool_markup_from_top_level_text():
    payload = {
        "text": 'schedule_task{"description":"Foot tapping reminder","delay_minutes":2}✅ Scheduled.'
    }
    text, reasoning = _extract(payload)
    assert text == "✅ Scheduled."
    assert reasoning == ""


def test_extract_strips_inline_tool_markup_from_output_text():
    payload = {
        "output_text": 'schedule_task{"instruction":"Remind user"}In two minutes, reminder set.'
    }
    text, _ = _extract(payload)
    assert text == "In two minutes, reminder set."
