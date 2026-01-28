"""
Conversation Memory Compression.

FEAT-003: Add summarization for old messages to bound memory usage.

When conversation history grows too large, older messages are
summarized into a compact context message, keeping recent messages
intact for context.

Usage:
    compressor = ConversationCompressor(llm_client)
    compressed = await compressor.compress(messages, keep_recent=10)
"""

import logging
from typing import Any, Optional

from ..state.models import ConversationMessage
from ..runtime.time_utils import utcnow

logger = logging.getLogger(__name__)


class ConversationCompressor:
    """Compresses conversation history to manage memory.

    Keeps recent messages intact while summarizing older ones
    into a compact system message.
    """

    def __init__(
        self,
        llm_client,
        max_summary_tokens: int = 500,
    ):
        """Initialize compressor.

        Args:
            llm_client: LLM client for generating summaries.
            max_summary_tokens: Target max tokens for summary.
        """
        self._llm_client = llm_client
        self._max_summary_tokens = max_summary_tokens

    async def compress(
        self,
        messages: list[ConversationMessage],
        keep_recent: int = 10,
    ) -> list[ConversationMessage]:
        """Compress conversation history.

        Args:
            messages: Full conversation history.
            keep_recent: Number of recent messages to keep intact.

        Returns:
            Compressed message list with summary + recent messages.
        """
        if len(messages) <= keep_recent:
            return messages

        # Split into old and recent
        old_messages = messages[:-keep_recent]
        recent_messages = messages[-keep_recent:]

        logger.info(
            "Compressing conversation: %d old messages into summary",
            len(old_messages),
        )

        # Generate summary of old messages
        summary = await self._summarize_messages(old_messages)

        # Create summary message
        summary_message = ConversationMessage(
            id=f"summary_{utcnow().timestamp()}",
            role="system",
            content=f"Earlier context (summarized from {len(old_messages)} messages):\n{summary}",
            metadata={
                "is_summary": True,
                "summarized_count": len(old_messages),
                "original_ids": [m.id for m in old_messages[:10]],  # First 10 for reference
            },
        )

        return [summary_message] + recent_messages

    async def _summarize_messages(
        self,
        messages: list[ConversationMessage],
    ) -> str:
        """Generate a summary of messages.

        Args:
            messages: Messages to summarize.

        Returns:
            Summary text.
        """
        # Format messages for summarization
        formatted = []
        for msg in messages:
            role = msg.role.upper()
            content = msg.content[:500] if len(msg.content) > 500 else msg.content
            formatted.append(f"[{role}]: {content}")

        conversation_text = "\n".join(formatted)

        # Generate summary
        prompt = f"""Summarize the following conversation concisely, preserving:
- Key topics discussed
- Important decisions made
- Action items or requests
- Any context needed for future messages

Conversation:
{conversation_text}

Summary (be concise, max ~{self._max_summary_tokens} tokens):"""

        try:
            summary = await self._llm_client.generate(
                prompt,
                system_prompt="You are a conversation summarizer. Be concise and preserve key context.",
            )
            return summary.strip()
        except Exception as e:
            logger.warning("Failed to generate summary: %s", e)
            # Fallback: simple truncation
            return self._fallback_summary(messages)

    def _fallback_summary(
        self,
        messages: list[ConversationMessage],
    ) -> str:
        """Create a fallback summary without LLM.

        Args:
            messages: Messages to summarize.

        Returns:
            Simple text summary.
        """
        topics = set()
        for msg in messages:
            # Extract simple keywords
            words = msg.content.lower().split()
            topics.update(w for w in words if len(w) > 4 and w.isalpha())

        top_topics = sorted(topics)[:20]

        return (
            f"Conversation of {len(messages)} messages. "
            f"Topics mentioned: {', '.join(top_topics[:10])}..."
        )

    def should_compress(
        self,
        messages: list[ConversationMessage],
        threshold: int = 50,
    ) -> bool:
        """Check if compression is needed.

        Args:
            messages: Current message list.
            threshold: Message count threshold for compression.

        Returns:
            True if compression is recommended.
        """
        return len(messages) > threshold

    def estimate_tokens(
        self,
        messages: list[ConversationMessage],
    ) -> int:
        """Estimate token count for messages.

        Args:
            messages: Messages to estimate.

        Returns:
            Estimated token count.
        """
        # Rough estimate: 4 chars per token
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // 4


class IncrementalCompressor(ConversationCompressor):
    """Compressor that maintains rolling summaries.

    Incrementally updates the summary as new messages arrive,
    avoiding full reprocessing of history.
    """

    def __init__(
        self,
        llm_client,
        max_summary_tokens: int = 500,
        compression_threshold: int = 20,
    ):
        """Initialize incremental compressor.

        Args:
            llm_client: LLM client.
            max_summary_tokens: Target summary size.
            compression_threshold: Messages before incremental compression.
        """
        super().__init__(llm_client, max_summary_tokens)
        self._compression_threshold = compression_threshold
        self._current_summary: Optional[str] = None
        self._summarized_count: int = 0

    async def add_and_compress(
        self,
        messages: list[ConversationMessage],
        keep_recent: int = 10,
    ) -> list[ConversationMessage]:
        """Add messages and compress incrementally if needed.

        Args:
            messages: Current message list.
            keep_recent: Recent messages to keep.

        Returns:
            Potentially compressed message list.
        """
        # Check if we need to update the summary
        unsummarized = len(messages) - keep_recent - (1 if self._current_summary else 0)

        if unsummarized >= self._compression_threshold:
            # Need to compress more messages into summary
            to_summarize = messages[:unsummarized]

            if self._current_summary:
                # Incremental update
                new_summary = await self._update_summary(
                    self._current_summary,
                    to_summarize,
                )
            else:
                # First summary
                new_summary = await self._summarize_messages(to_summarize)

            self._current_summary = new_summary
            self._summarized_count = len(to_summarize)

            # Build compressed list
            summary_msg = ConversationMessage(
                id=f"summary_{utcnow().timestamp()}",
                role="system",
                content=f"Earlier context (summarized):\n{self._current_summary}",
                metadata={
                    "is_summary": True,
                    "summarized_count": self._summarized_count,
                },
            )

            return [summary_msg] + messages[-keep_recent:]

        # No compression needed
        return messages

    async def _update_summary(
        self,
        current_summary: str,
        new_messages: list[ConversationMessage],
    ) -> str:
        """Incrementally update an existing summary.

        Args:
            current_summary: Existing summary.
            new_messages: New messages to incorporate.

        Returns:
            Updated summary.
        """
        # Format new messages
        new_text = "\n".join(
            f"[{m.role.upper()}]: {m.content[:300]}"
            for m in new_messages[-10:]  # Last 10 new messages
        )

        prompt = f"""Update this conversation summary with the new messages.
Keep it concise (~{self._max_summary_tokens} tokens max).

Current Summary:
{current_summary}

New Messages:
{new_text}

Updated Summary:"""

        try:
            return await self._llm_client.generate(
                prompt,
                system_prompt="You are updating a conversation summary. Be concise.",
            )
        except Exception as e:
            logger.warning("Failed to update summary: %s", e)
            # Fallback: keep current summary
            return current_summary + f"\n(+{len(new_messages)} more messages)"
