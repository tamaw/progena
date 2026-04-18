"""Azure Responses API client for Builder planning."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, model_validator

from .config import LLMConfig
from .prompts import load_prompt

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


def _build_responses_endpoint(base_url: str) -> str:
    """Build a valid /responses endpoint from either base URL or full endpoint URL."""
    raw = (base_url or "").strip()
    if not raw:
        return "/responses"

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    endpoint_path = path if path.endswith("/responses") else f"{path}/responses"

    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, parsed.query, parsed.fragment))


def _close_object_schemas(node: Any) -> None:
    """Recursively enforce strict object schemas Azure Responses accepts."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            node.clear()
            node["$ref"] = ref
            return
        if node.get("type") == "object":
            node["additionalProperties"] = False
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties.keys())
        for value in node.values():
            _close_object_schemas(value)
        return
    if isinstance(node, list):
        for item in node:
            _close_object_schemas(item)


class BuildPlan(BaseModel):
    """Structured task metadata produced before execution starts."""

    task_name: str = Field(description="Short human-readable task name")
    work_item_id: str = Field(description="Stable work item identifier for logs and worktrees")
    branch_name: str = Field(description="Git branch name to use for the build")
    description: str = Field(description="Concise build description")
    copilot_goal: str = Field(default="", description="Primary implementation goal for Copilot ACP")
    success_criteria: list[str] = Field(
        default_factory=list,
        description="Expected outcomes or checks the build should satisfy",
    )
    validation_targets: list[str] = Field(
        default_factory=list,
        description="Validation commands or checks the build should care about",
    )

    @model_validator(mode="after")
    def _require_core_fields(self) -> BuildPlan:
        self.task_name = self.task_name.strip()
        self.work_item_id = self.work_item_id.strip()
        self.branch_name = self.branch_name.strip()
        self.description = self.description.strip()
        self.copilot_goal = self.copilot_goal.strip()
        self.success_criteria = [item.strip() for item in self.success_criteria if item.strip()]
        self.validation_targets = [item.strip() for item in self.validation_targets if item.strip()]

        if not self.task_name:
            raise ValueError("Plan must include a non-empty task_name.")
        if not self.work_item_id:
            raise ValueError("Plan must include a non-empty work_item_id.")
        if not self.branch_name:
            raise ValueError("Plan must include a non-empty branch_name.")
        if not self.description:
            raise ValueError("Plan must include a non-empty description.")
        return self


@dataclass
class PlannedBuild:
    """Validated planning metadata plus the model's reasoning summary."""

    plan: BuildPlan
    reasoning: str
    planned_at_utc: str


def _build_json_schema_format(response_model: type[BaseModel], schema_name: str) -> dict[str, Any]:
    schema = response_model.model_json_schema()
    _close_object_schemas(schema)
    if "properties" in schema:
        schema["required"] = list(schema["properties"].keys())
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
            "strict": True,
        },
    }


def _to_azure_text_format(response_format: dict[str, Any]) -> dict[str, Any]:
    """Normalize json_schema format for Azure Responses compatibility."""
    if response_format.get("type") != "json_schema":
        return response_format

    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return response_format

    return {
        "type": "json_schema",
        "name": json_schema.get("name") or "structured_output",
        "schema": json_schema.get("schema") or {},
        "strict": bool(json_schema.get("strict", True)),
    }


class BuilderClient:
    """Small wrapper for the Azure OpenAI /responses endpoint."""

    def __init__(
        self, config: LLMConfig | None = None, role: str | None = None, log_dir: Path | None = None
    ) -> None:
        self.config = config or LLMConfig.from_env()
        self.role = role
        self.model = self.config.get_model()
        self.endpoint_url = _build_responses_endpoint(self.config.base_url)
        self.log_dir = Path(log_dir) if log_dir else None
        api_key = self.config.api_key or ""
        headers = {
            "Content-Type": "application/json",
            "api-key": api_key,
        }
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120.0), headers=headers)

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> BuilderClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        del exc_type
        del exc_val
        del exc_tb
        await self.close()

    def _build_input_messages(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        allowed_roles = {"system", "user", "assistant", "tool"}

        if system_prompt:
            payload.append({"role": "system", "content": system_prompt})

        for message in messages:
            role = str(message.get("role", "user") or "user")
            if role not in allowed_roles:
                role = "user"

            content = message.get("content", "")
            if isinstance(content, list):
                if (
                    content
                    and isinstance(content[0], dict)
                    and content[0].get("type")
                    in {
                        "input_text",
                        "output_text",
                        "input_image",
                        "input_file",
                        "refusal",
                        "summary_text",
                        "tether_browsing_display",
                        "computer_screenshot",
                        "tool_call",
                        "tool_result",
                    }
                ):
                    wrapped_content: Any = content
                else:
                    text_parts: list[str] = []
                    for part in content:
                        if isinstance(part, dict):
                            text_parts.append(str(part.get("text", "")))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    wrapped_content = "".join(text_parts)
            else:
                wrapped_content = str(content)

            payload.append(
                {
                    "role": role,
                    "content": wrapped_content,
                }
            )
        return payload

    def _build_payload(
        self,
        input_messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
        include_reasoning: bool = False,
        temperature: float | None = None,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }
        if response_format is not None:
            payload["text"] = {"format": _to_azure_text_format(response_format)}
        if include_reasoning:
            payload["reasoning"] = {"summary": "auto"}
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    async def _post_response(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        response_format: dict[str, Any] | None = None,
        include_reasoning: bool = False,
        temperature: float | None = None,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        input_messages = self._build_input_messages(messages, system_prompt)
        payload = self._build_payload(
            input_messages,
            response_format=response_format,
            include_reasoning=include_reasoning,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            logger.debug("Posting to Azure OpenAI: %s (model=%s)", self.endpoint_url, self.model)
            response = await self.client.post(self.endpoint_url, json=payload)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
        except Exception as e:
            logger.error(
                "Azure OpenAI API error: %s. Endpoint: %s. API key configured: %s",
                str(e)[:200],
                self.endpoint_url,
                bool(self.config and self.config.api_key),
            )
            raise

    def _extract_text_and_reasoning(self, data: dict[str, Any]) -> tuple[str, str]:
        text = ""
        reasoning = ""
        text_parts: list[str] = []

        for key in ("output_text", "final_text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())

        for item in data.get("output", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get("type")
                    if part_type in {"output_text", "text"} and isinstance(part.get("text"), str):
                        text_parts.append(part["text"].strip())
                    if part_type in {"reasoning", "summary_text"} and isinstance(
                        part.get("text"), str
                    ):
                        reasoning = part["text"].strip()
            elif item.get("type") == "reasoning":
                summary = item.get("summary")
                if isinstance(summary, list):
                    summary_parts = [
                        str(entry.get("text", "")) for entry in summary if isinstance(entry, dict)
                    ]
                    reasoning = "".join(summary_parts).strip()
                elif isinstance(summary, str):
                    reasoning = summary.strip()

        data_reasoning = data.get("reasoning")
        if not reasoning and isinstance(data_reasoning, dict):
            summary = data_reasoning.get("summary")
            if isinstance(summary, str):
                reasoning = summary.strip()
            elif isinstance(summary, list):
                summary_parts = [
                    str(entry.get("text", "")) for entry in summary if isinstance(entry, dict)
                ]
                reasoning = "".join(summary_parts).strip()

        if text_parts:
            text = "".join(text_parts).strip()

        return text, reasoning

    async def chat_structured_with_reasoning(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type[TModel],
        schema_name: str,
        system_prompt: str,
        temperature: float | None = None,
        max_tokens: int = 8192,
    ) -> tuple[TModel, str]:
        async def _request_raw() -> tuple[str, str]:
            data = await self._post_response(
                messages,
                system_prompt=system_prompt,
                response_format=response_format,
                include_reasoning=True,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            response_text, reasoning = self._extract_text_and_reasoning(data)
            if not response_text:
                status = str(data.get("status") or "")
                details = data.get("incomplete_details")
                incomplete_reason = ""
                if isinstance(details, dict) and details.get("reason"):
                    incomplete_reason = str(details.get("reason"))

                filter_flags: list[str] = []
                for entry in data.get("content_filters", []):
                    if not isinstance(entry, dict):
                        continue
                    source = str(entry.get("source_type") or "unknown")
                    if entry.get("blocked") is True:
                        filter_flags.append(f"{source}:blocked")
                    results = entry.get("content_filter_results")
                    if isinstance(results, dict):
                        for name, result in results.items():
                            if isinstance(result, dict) and (
                                result.get("filtered") is True or result.get("detected") is True
                            ):
                                filter_flags.append(f"{source}:{name}")

                if incomplete_reason or filter_flags:
                    parts: list[str] = []
                    if status:
                        parts.append(f"status={status}")
                    if incomplete_reason:
                        parts.append(f"incomplete={incomplete_reason}")
                    if filter_flags:
                        parts.append(f"content_filters={','.join(filter_flags)}")
                    raise RuntimeError(
                        "Builder planning returned no structured content ("
                        + "; ".join(parts)
                        + ")."
                    )

                logger.error(
                    "No text extracted from API response. Full response: %s",
                    json.dumps(data, indent=2)[:1000],
                )
                raise RuntimeError("Builder planning returned no structured content")
            return response_text, reasoning

        response_format = _build_json_schema_format(response_model, schema_name)
        response_text, reasoning = await _request_raw()
        parsed = response_model.model_validate(json.loads(response_text))
        return parsed, reasoning

    async def plan_build(self, request_text: str, context: dict[str, Any]) -> PlannedBuild:
        """Run the structured build-metadata planning call."""
        context_json = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
        planned_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        system_prompt = load_prompt("planner_system.md").strip()
        user_message = load_prompt(
            "plan_request.md",
            request_text=request_text.strip(),
            context_json=context_json,
            current_datetime_utc=planned_at_utc,
        ).strip()
        plan, reasoning = await self.chat_structured_with_reasoning(
            [{"role": "user", "content": user_message}],
            response_model=BuildPlan,
            schema_name="builder_build_plan",
            system_prompt=system_prompt,
            max_tokens=8192,
        )
        logger.info(
            "Built build metadata task_name=%s branch=%s work_item_id=%s",
            plan.task_name,
            plan.branch_name,
            plan.work_item_id,
        )
        return PlannedBuild(plan=plan, reasoning=reasoning, planned_at_utc=planned_at_utc)
