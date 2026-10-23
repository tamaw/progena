"""Pure transport helpers for auxiliary model calls."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx


def _build_responses_endpoint(base_url: str) -> str:
    """Build a valid /responses endpoint from either base URL or full endpoint URL."""
    raw = (base_url or "").strip()
    if not raw:
        return "/responses"

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    endpoint_path = path if path.endswith("/responses") else f"{path}/responses"

    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, parsed.query, parsed.fragment))


def supports_temperature(model: str) -> bool:
    """Return True if temperature parameter is supported for ``model``."""
    unsupported_keywords = {"gpt-5.2-chat"}
    model_lower = model.lower()
    return not any(keyword in model_lower for keyword in unsupported_keywords)


def is_temperature_unsupported(response: httpx.Response | None) -> bool:
    """Detect unsupported temperature errors from provider responses."""
    if response is None:
        return False
    try:
        data = response.json()
        message = str(data.get("error", {}).get("message", ""))
    except Exception:
        message = response.text
    message_lower = message.lower()
    return "temperature" in message_lower and "unsupported" in message_lower
