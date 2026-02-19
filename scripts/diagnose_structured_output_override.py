"""Live diagnostic for structured output + Azure override mapping.

Runs three probes using current environment variables:
1) Current Progenitor LLMClient structured output path (LiteLLM)
2) Raw LiteLLM /responses call (same payload style)
3) Direct Azure /responses override probe with two payload mappings

Use this script to inspect raw response payloads and JSON parsing behavior.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import contextlib

from app.progenitor.config import LLMConfig
from app.progenitor.llm.client import LLMClient, _build_json_schema_format


class StructuredDiagResponse(BaseModel):
    """Simple schema for structured-output diagnostics."""

    ok: bool = Field(description="Must be true")
    source: str = Field(description="Responder identity")
    notes: str = Field(description="Brief diagnostic note")


def _mask(value: str | None, keep: int = 6) -> str:
    if not value:
        return "<missing>"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}...***"


def _truncate(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]"


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None

    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    if start == -1:
        return None

    decoder = json.JSONDecoder()
    try:
        parsed, _end = decoder.raw_decode(stripped[start:])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_azure_base(endpoint: str) -> str:
    endpoint = endpoint.strip()
    parsed = urlparse(endpoint)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return endpoint.rstrip("/")


def _to_azure_text_format(response_format: dict[str, Any]) -> dict[str, Any]:
        """Convert LiteLLM/OpenAI-style response_format to Azure Responses text.format shape.

        Input shape (current):
            {"type": "json_schema", "json_schema": {"name": ..., "schema": ..., "strict": ...}}

        Azure Responses expected under text.format:
            {"type": "json_schema", "name": ..., "schema": ..., "strict": ...}
        """
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


def _resolve_override_env() -> dict[str, str | None]:
    responses_url = os.getenv("PROGENITOR_AZURE_STRUCTUED_RESPONSES_URL")
    api_key = os.getenv("PROGENITOR_AZURE_STRUCTUED_API_KEY")
    model = os.getenv("PROGENITOR_AZURE_STRUCTUED_MODEL")

    final_api_key = api_key
    final_model = model
    final_url = responses_url
    api_version = None

    return {
        "override_url": final_url,
        "override_api_key": final_api_key,
        "override_model": final_model,
        "api_version": api_version,
        "raw_override_url": responses_url,
        "raw_endpoint": None,
    }


async def _probe_llm_client(response_format: dict[str, Any]) -> dict[str, Any]:
    config = LLMConfig.from_env()
    client = LLMClient(config=config)
    messages = [
        {
            "role": "user",
            "content": "Return structured output only. Set ok=true, source='litellm-path', and include a short notes message.",
        }
    ]

    try:
        response_text = await client.chat(
            messages=messages,
            response_format=response_format,
            temperature=0.1,
            max_tokens=300,
        )

        parsed_direct = None
        direct_error = None
        try:
            parsed_direct = json.loads(response_text)
        except Exception as exc:
            direct_error = str(exc)

        parsed_first = _extract_first_json_object(response_text)

        return {
            "response_text": response_text,
            "parsed_direct": parsed_direct,
            "direct_error": direct_error,
            "parsed_first_object": parsed_first,
        }
    finally:
        await client.close()


async def _probe_raw_litellm(response_format: dict[str, Any]) -> dict[str, Any]:
    config = LLMConfig.from_env()
    endpoint = f"{config.base_url.rstrip('/')}/responses"
    payload = {
        "model": config.model_default,
        "input": [
            {
                "role": "user",
                "content": "Return structured output only. Set ok=true, source='raw-litellm', and include a short notes message.",
            }
        ],
        "response_format": response_format,
        "max_output_tokens": 300,
        "temperature": 0.1,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key or 'unused'}",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.post(endpoint, json=payload, headers=headers)
        raw_text = response.text
        body = None
        with contextlib.suppress(Exception):
            body = response.json()

        return {
            "endpoint": endpoint,
            "status_code": response.status_code,
            "raw_text": raw_text,
            "json_body": body,
            "request_payload": payload,
        }


async def _probe_direct_azure(response_format: dict[str, Any]) -> dict[str, Any]:
    env = _resolve_override_env()
    url = env["override_url"]
    api_key = env["override_api_key"]
    model = env["override_model"]

    if not (url and api_key and model):
        return {
            "skipped": True,
            "reason": "Missing override URL/API key/model from env.",
            "env": env,
        }

    headers = {
        "Content-Type": "application/json",
        "api-key": api_key,
    }

    azure_text_format = _to_azure_text_format(response_format)

    lite_style_payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": "Return structured output only. Set ok=true, source='azure-lite-style', and include a short notes message.",
            }
        ],
        "response_format": response_format,
        "max_output_tokens": 300,
        "temperature": 0.1,
    }

    azure_native_payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": "Return structured output only. Set ok=true, source='azure-native-style', and include a short notes message.",
            }
        ],
        "text": {"format": response_format},
        "max_output_tokens": 300,
        "temperature": 0.1,
    }

    azure_native_flat_payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": "Return structured output only. Set ok=true, source='azure-native-flat', and include a short notes message.",
            }
        ],
        "text": {"format": azure_text_format},
        "max_output_tokens": 300,
        "temperature": 0.1,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        results = {}
        for name, payload in {
            "lite_style_payload": lite_style_payload,
            "azure_native_payload": azure_native_payload,
            "azure_native_flat_payload": azure_native_flat_payload,
        }.items():
            resp = await client.post(url, json=payload, headers=headers)
            body = None
            with contextlib.suppress(Exception):
                body = resp.json()

            result = {
                "status_code": resp.status_code,
                "raw_text": resp.text,
                "json_body": body,
                "request_payload": payload,
            }

            should_retry_without_temp = False
            if resp.status_code >= 400:
                error_message = ""
                if isinstance(body, dict):
                    error_message = str(body.get("error", {}).get("message", ""))
                if not error_message:
                    error_message = resp.text
                error_lower = error_message.lower()
                should_retry_without_temp = "temperature" in error_lower and "unsupported" in error_lower

            if should_retry_without_temp:
                retry_payload = dict(payload)
                retry_payload.pop("temperature", None)
                retry_resp = await client.post(url, json=retry_payload, headers=headers)
                retry_body = None
                with contextlib.suppress(Exception):
                    retry_body = retry_resp.json()
                result["retry_without_temperature"] = {
                    "status_code": retry_resp.status_code,
                    "raw_text": retry_resp.text,
                    "json_body": retry_body,
                    "request_payload": retry_payload,
                }

            results[name] = result

    return {
        "skipped": False,
        "env": {
            **env,
            "override_api_key": _mask(api_key),
        },
        "results": results,
    }


async def main() -> int:
    load_dotenv()

    response_format = _build_json_schema_format(StructuredDiagResponse, "structured_diag_response")

    print("=" * 80)
    print("Structured Output Diagnostic (LiteLLM + Azure Override)")
    print("=" * 80)
    print("Env summary:")
    print(f"- LITELLM_BASE_URL={os.getenv('LITELLM_BASE_URL', '<missing>')}")
    print(f"- PROGENITOR_MODEL_DEFAULT={os.getenv('PROGENITOR_MODEL_DEFAULT', '<missing>')}")
    print(f"- LITELLM_MASTER_KEY={_mask(os.getenv('LITELLM_MASTER_KEY'))}")
    print(f"- PROGENITOR_AZURE_STRUCTUED_RESPONSES_URL={os.getenv('PROGENITOR_AZURE_STRUCTUED_RESPONSES_URL', '<missing>')}")
    print(f"- PROGENITOR_AZURE_STRUCTUED_MODEL={os.getenv('PROGENITOR_AZURE_STRUCTUED_MODEL', '<missing>')}")

    print("\n[1/3] Probing LLMClient structured output path...")
    llm_probe = await _probe_llm_client(response_format)
    print("LLMClient response_text:")
    print(_truncate(llm_probe["response_text"]))
    print("LLMClient parse(json.loads):", "ok" if llm_probe["parsed_direct"] is not None else f"fail ({llm_probe['direct_error']})")
    print("LLMClient parse(first-object):", "ok" if llm_probe["parsed_first_object"] is not None else "fail")
    if llm_probe["parsed_first_object"] is not None:
        print("LLMClient first-object parsed:")
        print(json.dumps(llm_probe["parsed_first_object"], indent=2))

    print("\n[2/3] Probing raw LiteLLM /responses payload...")
    raw_litellm = await _probe_raw_litellm(response_format)
    print(f"Raw LiteLLM status={raw_litellm['status_code']} endpoint={raw_litellm['endpoint']}")
    if raw_litellm["json_body"] is not None:
        print("Raw LiteLLM json body:")
        print(_truncate(json.dumps(raw_litellm["json_body"], indent=2)))
    else:
        print("Raw LiteLLM text body:")
        print(_truncate(raw_litellm["raw_text"]))

    print("\n[3/3] Probing direct Azure override mapping...")
    azure_probe = await _probe_direct_azure(response_format)
    if azure_probe.get("skipped"):
        print("Azure override probe: SKIPPED")
        print(f"Reason: {azure_probe['reason']}")
        print("Resolved override env:")
        print(json.dumps(azure_probe.get("env", {}), indent=2))
    else:
        print("Resolved override env:")
        print(json.dumps(azure_probe["env"], indent=2))
        for name, result in azure_probe["results"].items():
            print(f"\n{name}: status={result['status_code']}")
            if result["json_body"] is not None:
                print(_truncate(json.dumps(result["json_body"], indent=2)))
            else:
                print(_truncate(result["raw_text"]))

            retry_result = result.get("retry_without_temperature")
            if isinstance(retry_result, dict):
                print(f"{name} (retry_without_temperature): status={retry_result['status_code']}")
                if retry_result.get("json_body") is not None:
                    print(_truncate(json.dumps(retry_result["json_body"], indent=2)))
                else:
                    print(_truncate(str(retry_result.get("raw_text", ""))))

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
