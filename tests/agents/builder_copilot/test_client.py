"""Tests for the Builder Copilot planning client."""

import pytest

from app.agents.builder_copilot.client import (
    BuilderCopilotClient,
    PlanResponse,
    _build_responses_endpoint,
)
from app.agents.builder_copilot.config import LLMConfig


def test_build_responses_endpoint_keeps_existing_query_and_path():
    endpoint = _build_responses_endpoint(
        "https://example.openai.azure.com/openai/v1/responses?api-version=preview"
    )
    assert endpoint == "https://example.openai.azure.com/openai/v1/responses?api-version=preview"


def test_build_input_messages_normalizes_role_and_content():
    client = BuilderCopilotClient(LLMConfig(base_url="https://example.openai.azure.com/openai/v1"))
    payload = client._build_input_messages(
        [{"role": "scheduler", "content": [{"text": "hello"}, {"text": " world"}]}],
        system_prompt="system",
    )
    assert payload[0] == {"role": "system", "content": "system"}
    assert payload[1] == {"role": "user", "content": "hello world"}


def test_build_payload_includes_reasoning_and_structured_output():
    client = BuilderCopilotClient(LLMConfig(base_url="http://localhost:4000", api_key="test-key"), role="planner")
    payload = client._build_payload(
        [{"role": "user", "content": "plan this"}],
        response_format={"type": "json_schema", "json_schema": {"name": "plan", "schema": {}}},
        include_reasoning=True,
    )

    assert payload["model"] == client.model
    assert payload["reasoning"] == {"summary": "auto"}
    assert payload["text"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_plan_step_parses_structured_response_and_reasoning():
    client = BuilderCopilotClient(LLMConfig(base_url="http://localhost:4000"), role="planner")

    async def fake_post_response(*args, **kwargs):
        del args
        del kwargs
        return {
            "output_text": (
                '{"summary":"Implement and validate the change.",'
                '"steps":[{"step":"plan","objective":"Inspect the request","commands":["rg -n"]}],'
                '"risks":["Unknown test coverage"]}'
            ),
            "reasoning": {"summary": "Inspect first, then make the smallest safe change."},
        }

    client._post_response = fake_post_response  # type: ignore[method-assign]

    result = await client.plan_step("fix the bug", {"branch": "main"})

    assert isinstance(result.plan, PlanResponse)
    assert result.plan.steps[0].step == "plan"
    assert "smallest safe change" in result.reasoning
