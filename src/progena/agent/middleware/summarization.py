"""Progena's presentation layer over DeepAgents' native summarizer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deepagents.middleware.summarization import (
    SummarizationMiddleware as NativeSummarizationMiddleware,
)
from deepagents.middleware.summarization import (
    compute_summarization_defaults,
)
from deepagents.profiles import HarnessProfile, register_harness_profile
from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately

from src.progena.sessions.summarization import SUMMARY_MARKER_KEY, summary_marker

PROGENA_SUMMARY_PROMPT = """\
You are compacting prior conversation context for another assistant turn.
Preserve decisions, user intent, completed and pending work, exact identifiers,
constraints, errors, and file paths needed to continue correctly. Preserve supplied
`/conversations/` references exactly; never invent a conversation filename. Be concise;
omit conversational filler and do not answer the user.

<messages>
Messages to summarize:
{messages}
</messages>
"""

_PROFILE_INSTALLED = False


def install_progena_summarizer_profile() -> None:
    """Exclude the DeepAgents core instance through its supported profile API."""
    global _PROFILE_INSTALLED
    if _PROFILE_INSTALLED:
        return
    register_harness_profile(
        "progena_provider",
        HarnessProfile(excluded_middleware=frozenset({NativeSummarizationMiddleware})),
    )
    _PROFILE_INSTALLED = True


class ProgenaSummarizationMiddleware(NativeSummarizationMiddleware):
    """Keep native triggering, truncation and archive offload with Progena text."""

    @property
    def name(self) -> str:
        """Replace the native slot and inherit into the default subagent."""
        return "SummarizationMiddleware"

    def __init__(
        self,
        model: Any,
        *,
        backend: Any,
        summary_prompt: str = PROGENA_SUMMARY_PROMPT,
        **kwargs: Any,
    ) -> None:
        defaults = compute_summarization_defaults(model)
        kwargs.setdefault("trigger", defaults["trigger"])
        kwargs.setdefault("keep", defaults["keep"])
        kwargs.setdefault("truncate_args_settings", defaults["truncate_args_settings"])
        kwargs.setdefault("trim_tokens_to_summarize", None)
        kwargs.setdefault("token_counter", count_tokens_approximately)
        self.summary_prompt = summary_prompt
        self.trigger = kwargs["trigger"]
        self.keep = kwargs["keep"]
        self.truncate_args_settings = kwargs["truncate_args_settings"]
        self.trim_tokens_to_summarize = kwargs["trim_tokens_to_summarize"]
        super().__init__(model=model, backend=backend, summary_prompt=summary_prompt, **kwargs)

    def _conversation_mount_available(self) -> bool:
        routes = getattr(self._backend, "routes", None)
        return isinstance(routes, Mapping) and "/conversations/" in routes

    def _build_new_messages_with_path(
        self, summary: str, file_path: str | None
    ) -> list[AnyMessage]:
        """Attach display-safe metadata while preserving native summary delimiters."""
        retrieval_note = (
            "\n\nFor details from the current chat, search or read the canonical "
            "`/conversations/` mount."
            if self._conversation_mount_available()
            else ""
        )
        if file_path is None:
            content = f"""\
Conversation context has been compacted.{retrieval_note}

<summary>
{summary}
</summary>"""
        else:
            content = f"""\
Conversation context has been compacted. The offloaded history is available at
{file_path} if you need details.{retrieval_note}

<summary>
{summary}
</summary>"""
        return [
            HumanMessage(
                content=content,
                additional_kwargs={
                    "lc_source": "summarization",
                    SUMMARY_MARKER_KEY: summary_marker(summary, file_path),
                },
            )
        ]
