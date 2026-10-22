"""Validated execution-policy documents shared by UI, virtual files and runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.progenitor.documents.catalog import load_schema
from app.progenitor.documents.kinds import POLICY_KEY_PREFIX as POLICY_KEY_PREFIX
from app.progenitor.llm.structured import validate_against_schema

DEFAULT_POLICY_ID = "default"
CACHE_NODE_KINDS = frozenset({"prompt", "classify", "eval"})


class PolicyRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_retries: int = Field(default=0, ge=0)
    initial_interval: float = Field(default=0.1, ge=0)
    backoff_factor: float = Field(default=2, ge=1)
    max_interval: float = Field(default=5, ge=0)
    jitter: bool = True
    retry_on: Literal["transient", "model_response", "all"] = "transient"


class PolicyValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retry: PolicyRetry = Field(default_factory=PolicyRetry)
    attempt_timeout: float = Field(default=120, ge=0)
    idle_timeout: float | None = Field(default=None, gt=0)
    default_model_profile: str | None = Field(default=None, min_length=1)
    graph_step_limit: int = Field(default=1000, ge=1)
    concurrency_limit: int = Field(default=4, ge=1)
    default_fan_out_limit: int | None = Field(default=None, ge=1)
    cache_enabled: bool = False
    cache_ttl: int | None = Field(default=None, ge=0)


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1)
    description: str = ""
    version: Literal["1.0.0"] = "1.0.0"
    values: PolicyValues = Field(default_factory=PolicyValues)


def validate_policy(payload: Any) -> ExecutionPolicy:
    error = validate_against_schema(payload, load_schema("policy"))
    if error:
        raise ValueError(f"Invalid policy: {error}")
    return ExecutionPolicy.model_validate(payload)


def parse_policy_document(content: str) -> dict[str, Any]:
    return validate_policy(json.loads(content)).model_dump(mode="json")


def policy_revision_id(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def resolve_execution_policy(
    state_manager: Any, policy_id: str, revision_reader: Any
) -> dict[str, Any]:
    document = await state_manager.get_execution_policy(policy_id)
    if document is None:
        raise ValueError(f"Unknown execution policy '{policy_id}'")
    policy = validate_policy(document)
    if policy.id != policy_id:
        raise ValueError("Policy id does not match its document key")
    return {
        "id": policy.id,
        "revision_id": await revision_reader.fetch_policy_revision_id(policy_id, document),
        "values": policy.values.model_dump(mode="json"),
    }
