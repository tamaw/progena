"""Chat session documents shared by the backend and the runtime.

The backend owns these documents (one per chat under ``backend.chat.sessions``
plus an ``__index__`` list of thread ids); the runtime reads them to know
which chats exist, their titles, and whether they are archived. The title
rule lives here so the chat sidebar and the agent's transcripts never disagree.
"""

from __future__ import annotations

CHAT_METADATA_PREFIX = "backend.chat.sessions"
CHAT_SESSIONS_NAMESPACE = tuple(CHAT_METADATA_PREFIX.split("."))
CHAT_INDEX_KEY = "__index__"
DEFAULT_CHAT_TITLE = "New chat"
_PLACEHOLDER_TITLES = {DEFAULT_CHAT_TITLE.lower(), "new conversation", "untitled chat"}
_DERIVED_TITLE_CHARS = 64


def display_title(raw_title: str, first_user_message: str) -> str:
    """The title the chat sidebar shows.

    A placeholder title ("New chat", ...) falls back to the first user message,
    whitespace-collapsed and cut to 64 characters.
    """
    title = raw_title.strip() or "Untitled chat"
    if title.lower() not in _PLACEHOLDER_TITLES:
        return title
    normalized = " ".join(first_user_message.split())
    return normalized[:_DERIVED_TITLE_CHARS] if normalized else title
