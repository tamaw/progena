"""Project native DeepAgents summarization callbacks into the stream protocol."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler

from .stream_events import emit_stream_event


class SummarizationCallbackObserver(AsyncCallbackHandler):
    """Emit lifecycle activity for native summary-model calls only.

    DeepAgents labels the private summary call with ``lc_source=summarization``.
    The observer intentionally does not inspect model output: a completed model
    callback is not proof that the following main model call committed the
    returned summary event.
    """

    # Keep get_stream_writer() in the active LangGraph context and make the
    # lifecycle edge observable before a summary model can stream a chunk.
    run_inline = True

    def __init__(
        self, scope_for_metadata: Callable[[dict[str, Any]], dict[str, str] | None]
    ) -> None:
        super().__init__()
        self._scope_for_metadata = scope_for_metadata
        self._runs: dict[str, tuple[dict[str, str], str | None]] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del serialized, messages, kwargs
        if not _is_summarization_metadata(metadata):
            return
        scope = self._scope_for_metadata(metadata or {})
        if scope is None:
            return
        summary_id = str(run_id)
        namespace = _checkpoint_namespace(metadata)
        self._runs[summary_id] = (scope, namespace)
        emit_stream_event(
            {
                "event_type": "conversation_summary_started",
                "metadata": {"summary_id": summary_id, "scope": scope},
            }
        )

    async def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        del response, kwargs
        self._finish(str(run_id))

    async def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        del error, kwargs
        self._finish(str(run_id))

    def suppresses_message_stream(self, metadata: Any) -> bool:
        """Whether a messages-mode chunk belongs to a still-running summary call."""
        if not isinstance(metadata, dict):
            return False
        return _is_summarization_metadata(metadata) or (
            (namespace := _checkpoint_namespace(metadata)) is not None
            and namespace in _active_summary_namespaces(self._runs)
        )

    def _finish(self, summary_id: str) -> None:
        run = self._runs.pop(summary_id, None)
        if run is None:
            return
        scope, _namespace = run
        # A callback end/error is only a transient UI edge. The persisted
        # history projection establishes whether the native Command committed.
        emit_stream_event(
            {
                "event_type": "conversation_summary_finished",
                "metadata": {"summary_id": summary_id, "scope": scope},
            }
        )


def _is_summarization_metadata(metadata: Any) -> bool:
    return isinstance(metadata, dict) and metadata.get("lc_source") == "summarization"


def _checkpoint_namespace(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("langgraph_checkpoint_ns")
    return value if isinstance(value, str) and value else None


def _active_summary_namespaces(
    runs: Mapping[str, tuple[dict[str, str], str | None]],
) -> set[str]:
    return {namespace for _scope, namespace in runs.values() if namespace}
