"""Chat session title rules shared by the backend and the runtime."""

from __future__ import annotations

from src.progena.security.redaction import redact_secrets

DEFAULT_CHAT_TITLE = "New chat"
DEFAULT_USER_NICK = "user"
# Precedes the machine-only attachment note the backend appends to a user's
# message (``main._resolve_message_attachments``): the LLM needs it, but it is
# cut back off wherever the message is shown or stored as chat text.
ATTACHMENT_CONTEXT_MARKER = "<!--progena:attachment-context-->"
# Lower-case stored titles that mean "not named yet": the sidebar shows the first message.
PLACEHOLDER_TITLES = frozenset({DEFAULT_CHAT_TITLE.lower(), "new conversation", "untitled chat"})
_DERIVED_TITLE_CHARS = 64
# The conversation list's second line: the latest message, cut at write time.
SESSION_PREVIEW_CHARS = 200


def display_title(raw_title: str, first_user_message: str) -> str:
    """The title the chat sidebar shows.

    A placeholder title ("New chat", ...) falls back to the first user message,
    secret-redacted, whitespace-collapsed and cut to 64 characters.
    """
    title = raw_title.strip() or "Untitled chat"
    if title.lower() not in PLACEHOLDER_TITLES:
        return title
    normalized = " ".join(redact_secrets(first_user_message).split())
    return normalized[:_DERIVED_TITLE_CHARS] if normalized else title


def session_preview(text: str) -> str:
    """The stored ``preview`` of a message: no attachment note, secret-redacted,
    whitespace-collapsed and cut to ``SESSION_PREVIEW_CHARS`` with an ellipsis.
    """
    visible = text.split(ATTACHMENT_CONTEXT_MARKER, 1)[0]
    normalized = " ".join(redact_secrets(visible).split())
    if len(normalized) <= SESSION_PREVIEW_CHARS:
        return normalized
    return normalized[: SESSION_PREVIEW_CHARS - 1].rstrip() + "…"
