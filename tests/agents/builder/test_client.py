"""Tests for the Builder planning client."""

import pytest

from app.agents.builder.client import (
    BuilderClient,
    BuildPlan,
    _build_json_schema_format,
    _build_responses_endpoint,
)
from app.agents.builder.config import LLMConfig


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
    client = BuilderClient(
        LLMConfig(base_url="http://localhost:4000", api_key="test-key"), role="planner"
    )
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
    response_format = _build_json_schema_format(BuildPlan, "builder_plan")
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "task_name",
        "work_item_id",
        "branch_name",
        "description",
        "copilot_goal",
        "success_criteria",
        "validation_targets",
    }


@pytest.mark.asyncio
async def test_plan_build_parses_structured_response_and_reasoning():
    client = BuilderClient(LLMConfig(base_url="http://localhost:4000"), role="planner")

    async def fake_post_response(*args, **kwargs):
        del args
        del kwargs
        return {
            "output_text": (
                "{"
                '"task_name":"Scheduler ISO timestamp fix",'
                '"work_item_id":"scheduler-iso-fix",'
                '"branch_name":"progenitor/scheduler-iso-fix",'
                '"description":"Fix ISO timestamp parsing in scheduler.",'
                '"copilot_goal":"Update scheduler parsing and tests.",'
                '"success_criteria":["ISO timestamps stay intact"],'
                '"validation_targets":["pytest","ruff"]'
                "}"
            ),
            "reasoning": {"summary": "Extract stable metadata first."},
        }

    client._post_response = fake_post_response  # type: ignore[method-assign]

    result = await client.plan_build("fix the scheduler", {"branch": "main"})

    assert isinstance(result.plan, BuildPlan)
    assert result.plan.task_name == "Scheduler ISO timestamp fix"
    assert result.plan.branch_name == "progenitor/scheduler-iso-fix"
    assert result.plan.success_criteria == ["ISO timestamps stay intact"]
    assert result.planned_at_utc.endswith("Z")
    assert "stable metadata" in result.reasoning


@pytest.mark.asyncio
async def test_plan_build_parses_multipart_output_text_without_retry():
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
                            "text": '{"task_name":"Scheduler","work_item_id":"scheduler-fix",',
                        },
                        {
                            "type": "output_text",
                            "text": '"branch_name":"progenitor/scheduler-fix","description":"Fix it",'
                            '"copilot_goal":"Finish it","success_criteria":[],"validation_targets":["pytest"]}',
                        },
                    ],
                }
            ]
        }

    client._post_response = fake_post_response  # type: ignore[method-assign]

    result = await client.plan_build("fix the bug", {"branch": "main"})

    assert isinstance(result.plan, BuildPlan)
    assert result.plan.work_item_id == "scheduler-fix"
    assert result.plan.validation_targets == ["pytest"]


@pytest.mark.asyncio
async def test_plan_build_requires_core_fields():
    client = BuilderClient(LLMConfig(base_url="http://localhost:4000"), role="planner")

    async def fake_post_response(*args, **kwargs):
        del args
        del kwargs
        return {
            "output_text": (
                '{"task_name":"",'
                '"work_item_id":"scheduler-fix",'
                '"branch_name":"progenitor/scheduler-fix",'
                '"description":"Fix it",'
                '"copilot_goal":"Finish it",'
                '"success_criteria":[],'
                '"validation_targets":[]}'
            )
        }

    client._post_response = fake_post_response  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="task_name"):
        await client.plan_build("fix the bug", {"branch": "main"})


@pytest.mark.asyncio
async def test_plan_build_requests_reasoning():
    client = BuilderClient(LLMConfig(base_url="http://localhost:4000"), role="planner")

    async def fake_post_response(*args, **kwargs):
        del args
        assert kwargs["include_reasoning"] is True
        return {
            "output_text": (
                '{"task_name":"Scheduler",'
                '"work_item_id":"scheduler-fix",'
                '"branch_name":"progenitor/scheduler-fix",'
                '"description":"Fix it",'
                '"copilot_goal":"Finish it",'
                '"success_criteria":[],'
                '"validation_targets":["pytest","pyright","ruff"]}'
            ),
            "reasoning": {"summary": "Think first."},
        }

    client._post_response = fake_post_response  # type: ignore[method-assign]

    result = await client.plan_build("fix the bug", {"branch": "main"})

    assert result.reasoning == "Think first."
