"""Checkpoint-backed token accounting for the Insights UI.

This module reads LangGraph's immutable message-channel blobs directly.  It is a
reporting read model over checkpoint history, not a provider-call ledger.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    BackendDocumentStore,
    CheckpointMessageBlob,
)
from src.progena.settings.model_profiles import (
    MODEL_PROFILES_DOC_KEY,
    default_model_profiles_payload,
    validate_model_profiles_payload,
)

InsightsRange = Literal["24h", "7d", "30d"]

_RANGE_DURATION: dict[InsightsRange, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


@dataclass(frozen=True)
class _UsageCall:
    thread_id: str
    message_id: str
    observed_at: datetime
    occurred_at: datetime
    has_explicit_timestamp: bool
    profile_name: str | None
    deployment: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cache_read_tokens: int | None
    reasoning_tokens: int | None
    provider: str | None = None


class _Metrics:
    """Mutable arithmetic accumulator, serialized only at the API boundary."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.cache_read_tokens = 0
        self.reasoning_tokens = 0
        self.call_count = 0
        self.unknown_usage_count = 0
        self.unpriced_call_count = 0
        self._costs: dict[str, Decimal] = defaultdict(Decimal)

    def add(self, call: _UsageCall, pricing: Mapping[str, Any] | None, warnings: set[str]) -> None:
        self.call_count += 1
        self.input_tokens += call.input_tokens or 0
        self.output_tokens += call.output_tokens or 0
        self.total_tokens += call.total_tokens or 0
        self.cache_read_tokens += call.cache_read_tokens or 0
        self.reasoning_tokens += call.reasoning_tokens or 0
        if call.input_tokens is None or call.output_tokens is None or call.total_tokens is None:
            self.unknown_usage_count += 1
        cost = _estimate_cost(call, pricing, warnings)
        if cost is None:
            self.unpriced_call_count += 1
            return
        currency, amount = cost
        self._costs[currency] += amount

    def payload(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "call_count": self.call_count,
            "unknown_usage_count": self.unknown_usage_count,
            "unpriced_call_count": self.unpriced_call_count,
            "costs": [
                {"currency": currency, "amount": _decimal_string(amount)}
                for currency, amount in sorted(self._costs.items())
            ],
        }


def build_token_usage_snapshot(
    store: BackendDocumentStore,
    *,
    range_name: InsightsRange,
    profile: str | None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate unique completed model messages from persisted checkpoints.

    The single blob query includes all retained history before the
    request's end time. This is necessary for the first-observed fallback: a
    message copied into a newer checkpoint must retain the first checkpoint in
    which it appeared instead of being re-timestamped on every write.
    """
    if range_name not in _RANGE_DURATION:
        raise ValueError(f"Unsupported Insights range: {range_name}")
    if profile is not None and not profile.strip():
        raise ValueError("profile must be a non-empty string when supplied")

    end = (generated_at or datetime.now(UTC)).astimezone(UTC)
    start = end - _RANGE_DURATION[range_name]
    blobs, truncated = store.list_checkpoint_message_blobs(before=end)
    calls, decode_warnings = _unique_usage_calls(blobs)
    filtered_calls = [
        call
        for call in calls
        if start <= call.occurred_at < end and (profile is None or call.profile_name == profile)
    ]
    profiles = _load_profiles(store)
    warnings = set(decode_warnings)
    if truncated:
        warnings.add(
            "Checkpoint retention read reached its safe limit; older retained calls are excluded."
        )

    totals = _Metrics()
    by_profile: dict[tuple[str | None, str | None], _Metrics] = {}
    series: dict[datetime, _Metrics] = {}
    bucket_size = timedelta(hours=1) if range_name == "24h" else timedelta(days=1)
    for bucket_start in _bucket_starts(start, end, bucket_size):
        series[bucket_start] = _Metrics()

    for call in filtered_calls:
        pricing = (
            profiles.get(call.profile_name or "", {}).get("pricing") if call.profile_name else None
        )
        totals.add(call, pricing if isinstance(pricing, Mapping) else None, warnings)
        profile_metrics = by_profile.setdefault((call.profile_name, call.deployment), _Metrics())
        profile_metrics.add(call, pricing if isinstance(pricing, Mapping) else None, warnings)
        bucket_start = _floor_bucket(call.occurred_at, bucket_size)
        if bucket_start in series:
            series[bucket_start].add(
                call, pricing if isinstance(pricing, Mapping) else None, warnings
            )

    by_profile_rows = [
        {
            "profile_name": profile_name,
            "deployment": deployment,
            **metrics.payload(),
        }
        for (profile_name, deployment), metrics in sorted(
            by_profile.items(),
            key=lambda item: ((item[0][0] is None, item[0][0] or ""), item[0][1] or ""),
        )
    ]
    series_rows = [
        {"bucket_start": _iso(bucket_start), **metrics.payload()}
        for bucket_start, metrics in sorted(series.items())
    ]
    return {
        "range": range_name,
        "profile": profile,
        "start": _iso(start),
        "end": _iso(end),
        "generated_at": _iso(end),
        "totals": totals.payload(),
        "by_profile": by_profile_rows,
        "series": series_rows,
        "coverage": {
            "source": "checkpoints",
            "unattributed_call_count": sum(call.profile_name is None for call in filtered_calls),
            "timestamp_basis": "message_insights_or_provider_created_at_then_first_checkpoint_observation",
            "warnings": sorted(warnings),
        },
        "pricing_basis": "current_profile_rates",
    }


def _unique_usage_calls(blobs: list[CheckpointMessageBlob]) -> tuple[list[_UsageCall], set[str]]:
    """Decode trusted message blobs and retain the final state of each call."""
    serializer = JsonPlusSerializer(pickle_fallback=False)
    calls: dict[tuple[str], _UsageCall] = {}
    warnings: set[str] = set()
    # The store fetches newest-first for bounded current-window coverage; merge
    # in checkpoint order so an older repeated snapshot cannot overwrite a
    # completed later delta.
    for blob in sorted(
        blobs,
        key=lambda item: (
            item.checkpoint_ts,
            1 if item.source == "checkpoint_write" else 0,
            item.thread_id,
            item.checkpoint_id,
        ),
    ):
        if blob.blob_type != "msgpack":
            warnings.add("Unsupported checkpoint message serialization was excluded from Insights.")
            continue
        try:
            messages = serializer.loads_typed((blob.blob_type, blob.blob))
        except Exception:  # nosec B110 - corrupted historical blobs must not expose their contents
            warnings.add("Some checkpoint message blobs could not be decoded.")
            continue
        if not isinstance(messages, list):
            warnings.add("A checkpoint messages channel had an unexpected shape.")
            continue
        for message in messages:
            if _message_value(message, "type") == "ai" and not _nonempty_string(
                _message_value(message, "id")
            ):
                warnings.add("AI messages without stable IDs are excluded from Insights.")
                continue
            call = _usage_call_from_message(message, blob)
            if call is None:
                continue
            key = (call.message_id,)
            previous = calls.get(key)
            calls[key] = call if previous is None else _merge_usage_calls(previous, call)
    return list(calls.values()), warnings


def _merge_usage_calls(previous: _UsageCall, current: _UsageCall) -> _UsageCall:
    """Merge a later repeated message without losing earlier known dimensions."""
    first_observed = min(previous.observed_at, current.observed_at)
    if current.has_explicit_timestamp:
        occurred_at = current.occurred_at
        has_explicit_timestamp = True
    elif previous.has_explicit_timestamp:
        occurred_at = previous.occurred_at
        has_explicit_timestamp = True
    else:
        occurred_at = first_observed
        has_explicit_timestamp = False
    input_tokens = (
        current.input_tokens if current.input_tokens is not None else previous.input_tokens
    )
    output_tokens = (
        current.output_tokens if current.output_tokens is not None else previous.output_tokens
    )
    total_tokens = (
        current.total_tokens if current.total_tokens is not None else previous.total_tokens
    )
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return _UsageCall(
        thread_id=current.thread_id,
        message_id=current.message_id,
        observed_at=first_observed,
        occurred_at=occurred_at,
        has_explicit_timestamp=has_explicit_timestamp,
        profile_name=current.profile_name or previous.profile_name,
        deployment=current.deployment or previous.deployment,
        provider=current.provider or previous.provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=(
            current.cache_read_tokens
            if current.cache_read_tokens is not None
            else previous.cache_read_tokens
        ),
        reasoning_tokens=(
            current.reasoning_tokens
            if current.reasoning_tokens is not None
            else previous.reasoning_tokens
        ),
    )


def _usage_call_from_message(message: Any, blob: CheckpointMessageBlob) -> _UsageCall | None:
    if _message_value(message, "type") != "ai":
        return None
    message_id = _nonempty_string(_message_value(message, "id"))
    if message_id is None:
        return None
    response_metadata = _mapping_value(message, "response_metadata")
    usage_metadata = _mapping_value(message, "usage_metadata")
    if not usage_metadata and not response_metadata:
        # Synthetic summaries (e.g. nested routine nodes) never touched a provider.
        return None
    insights = response_metadata.get("insights")
    insights = insights if isinstance(insights, Mapping) else {}
    profile_name = _nonempty_string(insights.get("profile_name"))
    if profile_name is None:
        profile_name = _nonempty_string(blob.checkpoint_metadata.get("model_profile"))
    deployment = (
        _nonempty_string(insights.get("deployment"))
        or _nonempty_string(response_metadata.get("model_name"))
        or _nonempty_string(response_metadata.get("model"))
    )
    explicit_timestamp = _message_timestamp(insights, response_metadata)
    occurred_at = explicit_timestamp or blob.checkpoint_ts
    usage = _extract_usage(usage_metadata, response_metadata)
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    cache_read_tokens = _extract_detail(usage_metadata, response_metadata, name="cache_read")
    reasoning_tokens = _extract_detail(usage_metadata, response_metadata, name="reasoning")
    if (
        input_tokens is not None
        and cache_read_tokens is not None
        and cache_read_tokens > input_tokens
    ):
        cache_read_tokens = input_tokens
    return _UsageCall(
        thread_id=blob.thread_id,
        message_id=message_id,
        observed_at=blob.checkpoint_ts,
        occurred_at=occurred_at,
        has_explicit_timestamp=explicit_timestamp is not None,
        profile_name=profile_name,
        deployment=deployment,
        provider=_nonempty_string(insights.get("provider")),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _message_value(message: Any, key: str) -> Any:
    return message.get(key) if isinstance(message, Mapping) else getattr(message, key, None)


def _mapping_value(message: Any, key: str) -> Mapping[str, Any]:
    value = _message_value(message, key)
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _extract_usage(*containers: Mapping[str, Any]) -> dict[str, int]:
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    for container in _usage_containers(containers):
        result: dict[str, int] = {}
        for normalized, keys in aliases.items():
            for key in keys:
                value = container.get(key)
                if type(value) is int and value >= 0:
                    result[normalized] = value
                    break
        if result:
            return result
    return {}


def _extract_detail(*containers: Mapping[str, Any], name: str) -> int | None:
    aliases = {
        "cache_read": ("cache_read", "cached_tokens", "cache_read_tokens"),
        "reasoning": ("reasoning", "reasoning_tokens"),
    }[name]
    detail_keys = (
        ("input_token_details", "input_tokens_details", "prompt_tokens_details")
        if name == "cache_read"
        else ("output_token_details", "output_tokens_details", "completion_tokens_details")
    )
    for container in _usage_containers(containers):
        for detail_key in detail_keys:
            details = container.get(detail_key)
            if not isinstance(details, Mapping):
                continue
            for alias in aliases:
                value = details.get(alias)
                if type(value) is int and value >= 0:
                    return value
        for alias in aliases:
            value = container.get(alias)
            if type(value) is int and value >= 0:
                return value
    return None


def _usage_containers(containers: tuple[Mapping[str, Any], ...]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for container in containers:
        result.append(container)
        for key in ("token_usage", "usage"):
            nested = container.get(key)
            if isinstance(nested, Mapping):
                result.append(nested)
    return result


def _message_timestamp(
    insights: Mapping[str, Any], response_metadata: Mapping[str, Any]
) -> datetime | None:
    explicit = insights.get("occurred_at")
    if isinstance(explicit, str):
        try:
            value = datetime.fromisoformat(explicit.replace("Z", "+00:00"))
            return value.astimezone(UTC) if value.tzinfo is not None else None
        except ValueError:
            pass
    created_at = response_metadata.get("created_at")
    if isinstance(created_at, int | float) and not isinstance(created_at, bool):
        try:
            return datetime.fromtimestamp(float(created_at), UTC)
        except (OverflowError, OSError, ValueError):
            pass
    return None


def _estimate_cost(
    call: _UsageCall, pricing: Mapping[str, Any] | None, warnings: set[str]
) -> tuple[str, Decimal] | None:
    if call.provider == "chatgpt":
        warnings.add("ChatGPT plan usage is not priced using API token rates.")
        return None
    if call.input_tokens is None or call.output_tokens is None:
        return None
    if pricing is None:
        return None
    currency = _nonempty_string(pricing.get("currency"))
    input_rate = _decimal_rate(pricing.get("input_per_million"))
    output_rate = _decimal_rate(pricing.get("output_per_million"))
    cache_read = call.cache_read_tokens
    cached_rate = _decimal_rate(pricing.get("cached_input_per_million"))
    if currency is None or input_rate is None or output_rate is None:
        return None
    if cache_read is None:
        warnings.add(
            "Some calls do not report cache-read usage; estimates apply the standard input rate."
        )
        cache_read = 0
    if cache_read and cached_rate is None:
        return None
    amount = (
        (Decimal(call.input_tokens - cache_read) * input_rate)
        + (Decimal(cache_read) * (cached_rate or Decimal(0)))
        + (Decimal(call.output_tokens) * output_rate)
    )
    return currency, amount / Decimal(1_000_000)


def _decimal_rate(value: Any) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def _load_profiles(store: BackendDocumentStore) -> dict[str, dict[str, Any]]:
    document = store.get_document(PROGENA_STATE_PREFIX, MODEL_PROFILES_DOC_KEY)
    raw = document.payload if document is not None else default_model_profiles_payload()
    return validate_model_profiles_payload(raw)


def _floor_bucket(value: datetime, bucket_size: timedelta) -> datetime:
    value = value.astimezone(UTC)
    if bucket_size == timedelta(hours=1):
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _bucket_starts(start: datetime, end: datetime, bucket_size: timedelta) -> list[datetime]:
    bucket = _floor_bucket(start, bucket_size)
    values: list[datetime] = []
    while bucket < end:
        values.append(bucket)
        bucket += bucket_size
    return values


def _nonempty_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _decimal_string(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return "0" if rendered in {"", "-0"} else rendered


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
