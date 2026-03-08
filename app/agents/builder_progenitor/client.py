"""Azure Responses API client for Builder planning."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field

from .config import LLMConfig

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


class PlanStep(BaseModel):
    """A single execution step in the preflight plan."""

    step: str = Field(description="Short step name")
    objective: str = Field(description="What this step accomplishes")
    commands: list[str] = Field(default_factory=list, description="Suggested commands")


class PlanResponse(BaseModel):
    """Structured planning response."""

    summary: str = Field(default="", description="High-level plan summary")
    steps: list[PlanStep] = Field(default_factory=list, description="Ordered execution steps")
    risks: list[str] = Field(default_factory=list, description="Known risks or open questions")


@dataclass
class ReasonedPlan:
    """Validated planning output plus the model's reasoning summary."""

    plan: PlanResponse
    reasoning: str


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

    def __init__(self, config: LLMConfig | None = None, role: str | None = None) -> None:
        self.config = config or LLMConfig.from_env()
        self.role = role
        self.model = self.config.get_model_for_role(role)
        self.endpoint_url = _build_responses_endpoint(self.config.base_url)
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
                if content and isinstance(content[0], dict) and content[0].get("type") in {
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
                }:
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
            return response.json()
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

        for key in ("output_text", "final_text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break

        for item in data.get("output", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get("type")
                    if not text and part_type in {"output_text", "text"} and isinstance(part.get("text"), str):
                        text = part["text"].strip()
                    if part_type in {"reasoning", "summary_text"} and isinstance(part.get("text"), str):
                        reasoning = part["text"].strip()
            elif item.get("type") == "reasoning":
                summary = item.get("summary")
                if isinstance(summary, list):
                    summary_parts = [str(entry.get("text", "")) for entry in summary if isinstance(entry, dict)]
                    reasoning = "".join(summary_parts).strip()
                elif isinstance(summary, str):
                    reasoning = summary.strip()

        data_reasoning = data.get("reasoning")
        if not reasoning and isinstance(data_reasoning, dict):
            summary = data_reasoning.get("summary")
            if isinstance(summary, str):
                reasoning = summary.strip()
            elif isinstance(summary, list):
                summary_parts = [str(entry.get("text", "")) for entry in summary if isinstance(entry, dict)]
                reasoning = "".join(summary_parts).strip()

        return text, reasoning

    async def chat_structured_with_reasoning(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type[TModel],
        schema_name: str,
        system_prompt: str,
        temperature: float | None = None,
        max_tokens: int = 1200,
    ) -> tuple[TModel, str]:
        def _is_max_tokens_incomplete(payload: dict[str, Any]) -> bool:
            details = payload.get("incomplete_details")
            if not isinstance(details, dict):
                return False
            return details.get("reason") == "max_output_tokens"

        async def _request_raw(
            request_max_tokens: int,
            request_include_reasoning: bool,
        ) -> tuple[dict[str, Any], str, str]:
            data = await self._post_response(
                messages,
                system_prompt=system_prompt,
                response_format=response_format,
                include_reasoning=request_include_reasoning,
                temperature=temperature,
                max_tokens=request_max_tokens,
            )
            response_text, reasoning = self._extract_text_and_reasoning(data)
            if not response_text:
                logger.error(
                    "No text extracted from API response. Full response: %s",
                    json.dumps(data, indent=2)[:1000],
                )
                raise RuntimeError("Builder planning returned no structured content")
            return data, response_text, reasoning

        response_format = _build_json_schema_format(response_model, schema_name)
        retry_data: dict[str, Any] | None = None
        retry_text = ""
        try:
            _, response_text, reasoning = await _request_raw(
                request_max_tokens=max_tokens,
                request_include_reasoning=True,
            )
            parsed = response_model.model_validate(json.loads(response_text))
        except json.JSONDecodeError as e:
            # A common failure mode is truncation when output hits max_output_tokens.
            # Retry once with a larger budget and no reasoning summary to preserve tokens for JSON.
            try:
                retry_max_tokens = min(max(max_tokens * 2, 2400), 4096)
                logger.warning(
                    "Structured response JSON parse failed at char %d. Retrying with max_output_tokens=%d",
                    e.pos,
                    retry_max_tokens,
                )
                retry_data, retry_text, reasoning = await _request_raw(
                    request_max_tokens=retry_max_tokens,
                    request_include_reasoning=False,
                )
                parsed = response_model.model_validate(json.loads(retry_text))
                if _is_max_tokens_incomplete(retry_data):
                    logger.warning("Retry response still marked incomplete due to max_output_tokens")
                return parsed, reasoning
            except json.JSONDecodeError as retry_error:
                data = retry_data or {}
                response_text = retry_text
                logger.error(
                    "Failed to parse response as JSON (char %d). Response text (first 1000 chars):\n%s",
                    retry_error.pos,
                    response_text[:1000],
                )
                logger.error("Full API response: %s", json.dumps(data, indent=2)[:2000])

                incomplete_reason = ""
                details = data.get("incomplete_details")
                if isinstance(details, dict) and details.get("reason"):
                    incomplete_reason = f" API marked response incomplete: {details.get('reason')}."

                raise RuntimeError(
                    f"Invalid JSON in structured response at char {retry_error.pos}: {retry_error.msg}."
                    f"{incomplete_reason}"
                ) from retry_error
        return parsed, reasoning

    async def plan_step(self, request_text: str, context: dict[str, Any]) -> ReasonedPlan:
        """Run the dedicated reasoning-backed planning call."""
        context_json = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
        system_prompt = (
            "You are Builder's planning pass. Produce a compact execution plan before any edits. "
            "Return only JSON matching the schema. Keep the plan focused on: plan, build, test, lint, pr. "
            "Keep each command concise and avoid large inline file bodies (for example, use placeholders or "
            "short commands instead of full heredoc contents)."
        )
        user_message = (
            f"User request:\n{request_text.strip()}\n\n"
            f"Execution context (JSON):\n{context_json}\n\n"
            "Create a practical plan with the minimum useful steps."
        )
        plan, reasoning = await self.chat_structured_with_reasoning(
            [{"role": "user", "content": user_message}],
            response_model=PlanResponse,
            schema_name="builder_plan",
            system_prompt=system_prompt,
        )
        logger.info("Built reasoning-backed plan with %d step(s)", len(plan.steps))
        return ReasonedPlan(plan=plan, reasoning=reasoning)
