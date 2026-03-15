"""Tests for the Builder planning client."""

import pytest

from app.agents.builder_progenitor.client import (
    BuilderClient,
    PlanResponse,
    _build_json_schema_format,
    _build_responses_endpoint,
)
from app.agents.builder_progenitor.config import LLMConfig
from app.agents.builder_progenitor.prompts import load_prompt


def test_build_responses_endpoint_keeps_existing_query_and_path():
    endpoint = _build_responses_endpoint(
        "https://example.openai.azure.com/openai/v1/responses?api-version=preview"
    )
    assert endpoint == "https://example.openai.azure.com/openai/v1/responses?api-version=preview"


def test_build_input_messages_normalizes_role_and_content():
    client = BuilderClient(LLMConfig(base_url="https://example.openai.azure.com/openai/v1"))
    payload = client._build_input_messages(
        [{"role": "scheduler", "content": [{"text": "hello"}, {"text": " world"}]}],
        system_prompt="system",
    )
    assert payload[0] == {"role": "system", "content": "system"}
    assert payload[1] == {"role": "user", "content": "hello world"}


def test_build_payload_includes_reasoning_and_structured_output():
    client = BuilderClient(LLMConfig(base_url="http://localhost:4000", api_key="test-key"), role="planner")
    payload = client._build_payload(
        [{"role": "user", "content": "plan this"}],
        response_format={"type": "json_schema", "json_schema": {"name": "plan", "schema": {}}},
        include_reasoning=True,
    )

    assert payload["model"] == client.model
    assert payload["reasoning"] == {"summary": "auto"}
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["name"] == "plan"
    assert "json_schema" not in payload["text"]["format"]
    assert "temperature" not in payload


def test_schema_format_closes_nested_object_defs():
    response_format = _build_json_schema_format(PlanResponse, "builder_plan")
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"summary", "steps", "risks"}
    assert schema["$defs"]["PlanStep"]["additionalProperties"] is False
    assert set(schema["$defs"]["PlanStep"]["required"]) == {"step", "objective", "commands"}


@pytest.mark.asyncio
async def test_plan_step_parses_structured_response_and_reasoning():
    client = BuilderClient(LLMConfig(base_url="http://localhost:4000"), role="planner")

    async def fake_post_response(*args, **kwargs):
        del args
        del kwargs
        return {
            "output_text": (
                '{"summary":"Implement and validate the change.",'
                '"steps":['
                '{"step":"plan","objective":"Inspect the request","commands":["rg -n"]},'
                '{"step":"pr","objective":"Open PR","commands":["gh pr create"]}'
                '],'
                '"risks":["Unknown test coverage"]}'
            ),
            "reasoning": {"summary": "Inspect first, then make the smallest safe change."},
        }

    client._post_response = fake_post_response  # type: ignore[method-assign]

    result = await client.plan_step("fix the bug", {"branch": "main"})

    assert isinstance(result.plan, PlanResponse)
    assert result.plan.steps[0].step == "plan"
    assert "smallest safe change" in result.reasoning


@pytest.mark.asyncio
async def test_plan_step_parses_multipart_output_text_without_retry():
    client = BuilderClient(LLMConfig(base_url="http://localhost:4000"), role="planner")

    async def fake_post_response(*args, **kwargs):
        del args
        del kwargs
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"summary":"s","steps":[{"step":"plan",',
                        },
                        {
                            "type": "output_text",
                            "text": '"objective":"o","commands":["echo ok"]},{"step":"pr","objective":"Open PR","commands":["gh pr create"]}],"risks":[]}',
                        },
                    ],
                }
            ]
        }

    client._post_response = fake_post_response  # type: ignore[method-assign]

    result = await client.plan_step("fix the bug", {"branch": "main"})

    assert isinstance(result.plan, PlanResponse)
    assert result.plan.summary == "s"
    assert result.plan.steps[0].commands == ["echo ok"]


@pytest.mark.asyncio
async def test_plan_step_requires_pr_step():
    client = BuilderClient(LLMConfig(base_url="http://localhost:4000"), role="planner")

    async def fake_post_response(*args, **kwargs):
        del args
        del kwargs
        return {
            "output_text": (
                '{"summary":"Implement only","steps":[{"step":"plan","objective":"Inspect","commands":[]}],"risks":[]}'
            )
        }

    client._post_response = fake_post_response  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Plan must include a 'pr' step"):
        await client.plan_step("fix the bug", {"branch": "main"})


@pytest.mark.asyncio
async def test_plan_step_requests_reasoning():
    client = BuilderClient(LLMConfig(base_url="http://localhost:4000"), role="planner")

    async def fake_post_response(*args, **kwargs):
        del args
        assert kwargs["include_reasoning"] is True
        assert kwargs["system_prompt"] == load_prompt("planner_system.md").strip()
        return {
            "output_text": (
                '{"summary":"Implement and validate the change.",'
                '"steps":['
                '{"step":"plan","objective":"Inspect the request","commands":[]},'
                '{"step":"pr","objective":"Open PR","commands":["gh pr create"]}'
                '],'
                '"risks":[]}'
            ),
            "reasoning": {"summary": "Think first."},
        }

    client._post_response = fake_post_response  # type: ignore[method-assign]

    result = await client.plan_step("fix the bug", {"branch": "main"})

    assert result.reasoning == "Think first."
