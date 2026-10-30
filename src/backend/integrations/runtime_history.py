"""Sync reader for native runtime conversation projections.

The backend never rebuilds agent messages from checkpoint blobs itself; it asks
the owning runtime for one lossless native message list per checkpoint revision.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.messages import messages_from_dict

from src.progena.sessions.models import (
    CONVERSATION_MESSAGES_FIELD,
    CONVERSATION_MESSAGES_INCLUDE,
    CONVERSATION_SUMMARIES_FIELD,
    CONVERSATION_SUMMARIES_INCLUDE,
)

logger = logging.getLogger(__name__)

MAX_CACHE_ENTRIES = 256
# After a connection failure, skip calling the runtime for this long so a down
# or restarting runtime fails fast instead of every chat read timing out.
UNAVAILABLE_COOLDOWN_SECONDS = 5.0
REQUEST_TIMEOUT = httpx.Timeout(5.0, connect=1.0)


@dataclass(frozen=True)
class RuntimeSummaryOccurrence:
    """Committed native summary occurrence projected by the runtime."""

    id: str
    summary: str
    created_at_index: int
    anchor_message_id: str | None
    scope: dict[str, str]
    committed_at: str | None


class RuntimeUnavailable(RuntimeError):
    """The runtime could not be reached and no earlier copy of the thread is known."""


class RuntimeConversationReader:
    """Reads native conversation messages with exact checkpoint-keyed caching.

    Results are cached per ``(thread_id, checkpoint_id)``: a checkpoint never
    changes, so a hit is always exact. While the runtime is unreachable, the
    latest copy seen for a thread is served instead (stale but real history);
    only a thread never seen before raises ``RuntimeUnavailable``.
    """

    def __init__(self, base_url: str) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=REQUEST_TIMEOUT)
        self._cache: OrderedDict[tuple[str, str], list[Any]] = OrderedDict()
        self._summary_cache: OrderedDict[tuple[str, str], tuple[RuntimeSummaryOccurrence, ...]] = (
            OrderedDict()
        )
        self._latest_by_thread: dict[str, list[Any]] = {}
        self._unavailable_until = 0.0
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def get_messages(
        self,
        thread_id: str,
        checkpoint_id: str | None = None,
        *,
        exact_revision: bool = False,
    ) -> list[Any]:
        """LangChain messages at ``checkpoint_id`` (latest when omitted).

        Ordinary reads may use the last observed thread state during a runtime
        outage. Callers combining messages with checkpoint-scoped data must
        pass ``exact_revision=True`` so an unavailable revision cannot be
        paired with data from another checkpoint.
        """
        if exact_revision and not checkpoint_id:
            raise RuntimeUnavailable("an exact message read requires a checkpoint id")
        cache_key = (thread_id, checkpoint_id or "")
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return list(cached)
            cooling_down = time.monotonic() < self._unavailable_until

        try:
            if cooling_down:
                raise RuntimeUnavailable("runtime marked unavailable")
            messages = self._fetch(thread_id, checkpoint_id)
        except RuntimeUnavailable:
            if exact_revision:
                raise
            with self._lock:
                latest = self._latest_by_thread.get(thread_id)
            if latest is None:
                raise
            return list(latest)

        with self._lock:
            self._cache[cache_key] = messages
            self._cache.move_to_end(cache_key)
            while len(self._cache) > MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)
            self._latest_by_thread[thread_id] = messages
        return list(messages)

    def get_summary_occurrences(
        self, thread_id: str, checkpoint_id: str | None = None
    ) -> list[RuntimeSummaryOccurrence]:
        """Committed summary occurrences for one immutable checkpoint revision."""
        cache_key = (thread_id, checkpoint_id) if checkpoint_id else None
        with self._lock:
            cached = self._summary_cache.get(cache_key) if cache_key is not None else None
            if cached is not None and cache_key is not None:
                self._summary_cache.move_to_end(cache_key)
                return list(cached)
            cooling_down = time.monotonic() < self._unavailable_until

        if cooling_down:
            raise RuntimeUnavailable("runtime marked unavailable")
        summaries = self._fetch_summary_occurrences(thread_id, checkpoint_id)

        if cache_key is not None:
            with self._lock:
                self._summary_cache[cache_key] = tuple(summaries)
                self._summary_cache.move_to_end(cache_key)
                while len(self._summary_cache) > MAX_CACHE_ENTRIES:
                    self._summary_cache.popitem(last=False)
        return list(summaries)

    def _fetch(self, thread_id: str, checkpoint_id: str | None) -> list[Any]:
        params: dict[str, Any] = {
            "order": "asc",
            "limit": 1,
            "include": CONVERSATION_MESSAGES_INCLUDE,
        }
        if checkpoint_id:
            params["checkpoint_id"] = checkpoint_id
        try:
            response = self._client.get(f"/v1/conversations/{thread_id}/items", params=params)
        except httpx.TransportError as exc:
            with self._lock:
                self._unavailable_until = time.monotonic() + UNAVAILABLE_COOLDOWN_SECONDS
            raise RuntimeUnavailable(f"runtime unreachable: {exc}") from exc
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise RuntimeUnavailable(
                f"runtime returned {response.status_code} for {thread_id}: {response.text[:200]}"
            )
        messages = response.json().get(CONVERSATION_MESSAGES_FIELD)
        if not isinstance(messages, list):
            raise RuntimeUnavailable("runtime did not return native conversation messages")
        return messages_from_dict(messages)

    def _fetch_summary_occurrences(
        self, thread_id: str, checkpoint_id: str | None
    ) -> list[RuntimeSummaryOccurrence]:
        params: dict[str, Any] = {
            "order": "asc",
            "limit": 1,
            "include": CONVERSATION_SUMMARIES_INCLUDE,
        }
        if checkpoint_id:
            params["checkpoint_id"] = checkpoint_id
        try:
            response = self._client.get(f"/v1/conversations/{thread_id}/items", params=params)
        except httpx.TransportError as exc:
            with self._lock:
                self._unavailable_until = time.monotonic() + UNAVAILABLE_COOLDOWN_SECONDS
            raise RuntimeUnavailable(f"runtime unreachable: {exc}") from exc
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise RuntimeUnavailable(
                f"runtime returned {response.status_code} for {thread_id}: {response.text[:200]}"
            )
        payload = response.json()
        raw_occurrences = payload.get(CONVERSATION_SUMMARIES_FIELD)
        if not isinstance(raw_occurrences, list):
            return []
        occurrences: list[RuntimeSummaryOccurrence] = []
        seen_ids: set[str] = set()
        for value in raw_occurrences:
            occurrence = _summary_occurrence(value)
            if occurrence is not None and occurrence.id not in seen_ids:
                seen_ids.add(occurrence.id)
                occurrences.append(occurrence)
        return occurrences


def _summary_occurrence(value: Any) -> RuntimeSummaryOccurrence | None:
    if not isinstance(value, dict):
        return None
    identifier = value.get("id")
    summary = value.get("summary")
    created_at_index = value.get("created_at_index")
    anchor_message_id = value.get("anchor_message_id")
    raw_scope = value.get("scope")
    if (
        not isinstance(identifier, str)
        or not identifier
        or not isinstance(summary, str)
        or not summary
        or not isinstance(created_at_index, int)
        or isinstance(created_at_index, bool)
        or created_at_index < 0
        or anchor_message_id is not None
        and (not isinstance(anchor_message_id, str) or not anchor_message_id)
        or not isinstance(raw_scope, dict)
    ):
        return None
    kind = raw_scope.get("kind")
    scope_id = raw_scope.get("id")
    label = raw_scope.get("label")
    if kind not in {"conversation", "subagent"} or not isinstance(scope_id, str) or not scope_id:
        return None
    scope = {"kind": kind, "id": scope_id}
    if isinstance(label, str) and label:
        scope["label"] = label
    committed_at = value.get("committed_at")
    return RuntimeSummaryOccurrence(
        id=identifier,
        summary=summary,
        created_at_index=created_at_index,
        anchor_message_id=anchor_message_id,
        scope=scope,
        committed_at=committed_at if isinstance(committed_at, str) and committed_at else None,
    )
