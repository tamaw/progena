"""Minimal LiteLLM client for Builder Copilot planning."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, Field

from .config import LLMConfig

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


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
    schema["additionalProperties"] = False
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


class BuilderCopilotClient:
    """Small wrapper for the LiteLLM /responses endpoint."""

    def __init__(self, config: LLMConfig | None = None, role: str | None = None) -> None:
        self.config = config or LLMConfig.from_env()
        self.role = role
        self.model = self.config.get_model_for_role(role)
        base_url = self.config.base_url.rstrip("/")
        self.endpoint_url = f"{base_url}/responses"
        api_key = self.config.api_key or ""
        headers = {
            "Content-Type": "application/json",
            "x-litellm-api-key": api_key,
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120.0), headers=headers)

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> "BuilderCopilotClient":
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
        if system_prompt:
            payload.append({"role": "system", "content": system_prompt})
        for message in messages:
            payload.append(
                {
                    "role": str(message.get("role", "user") or "user"),
                    "content": str(message.get("content", "")),
                }
            )
        return payload

    def _build_payload(
        self,
        input_messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
        include_reasoning: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }
        if self.config.model_fallbacks:
            payload["fallbacks"] = self.config.model_fallbacks
        if response_format is not None:
            payload["text"] = {"format": response_format}
        if include_reasoning:
            payload["reasoning"] = {"summary": "auto"}
        payload["temperature"] = temperature
        return payload

    async def _post_response(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        response_format: dict[str, Any] | None = None,
        include_reasoning: bool = False,
        temperature: float = 0.3,
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
        response = await self.client.post(self.endpoint_url, json=payload)
        response.raise_for_status()
        return response.json()

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
        temperature: float = 0.3,
        max_tokens: int = 1200,
    ) -> tuple[TModel, str]:
        response_format = _build_json_schema_format(response_model, schema_name)
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
            raise RuntimeError("Builder Copilot planning returned no structured content")
        parsed = response_model.model_validate(json.loads(response_text))
        return parsed, reasoning

    async def plan_step(self, request_text: str, context: dict[str, Any]) -> ReasonedPlan:
        """Run the dedicated reasoning-backed planning call."""
        context_json = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
        system_prompt = (
            "You are Builder Copilot's planning pass. Produce a compact execution plan before any edits. "
            "Return only JSON matching the schema. Keep the plan focused on: plan, build, test, lint, pr."
        )
        user_message = (
            f"User request:\n{request_text.strip()}\n\n"
            f"Execution context (JSON):\n{context_json}\n\n"
            "Create a practical plan with the minimum useful steps."
        )
        plan, reasoning = await self.chat_structured_with_reasoning(
            [{"role": "user", "content": user_message}],
            response_model=PlanResponse,
            schema_name="builder_copilot_plan",
            system_prompt=system_prompt,
        )
        logger.info("Built reasoning-backed plan with %d step(s)", len(plan.steps))
        return ReasonedPlan(plan=plan, reasoning=reasoning)
