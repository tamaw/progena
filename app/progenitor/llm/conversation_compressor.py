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

from ..runtime.time_utils import utcnow
from ..state.models import ConversationMessage
from .prompts import PromptTemplates
from .tokenizer import count_message_tokens

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()


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
        try:
            self._genesis_prompt = _prompts.get_genesis_prompt().strip()
        except Exception:
            self._genesis_prompt = ""

    def _is_genesis_message(self, message: ConversationMessage) -> bool:
        """Detect genesis bootstrap prompt messages in history."""
        return (
            message.role == "system"
            and bool(self._genesis_prompt)
            and message.content.strip() == self._genesis_prompt
        )

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
        filtered_messages = [m for m in messages if not self._is_genesis_message(m)]

        if len(filtered_messages) <= keep_recent:
            return filtered_messages

        # Split into old and recent
        old_messages = filtered_messages[:-keep_recent]
        recent_messages = filtered_messages[-keep_recent:]

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
        prompt = _prompts.get(
            "conversation_compressor.summarize.md",
            conversation_text=conversation_text,
            max_summary_tokens=str(self._max_summary_tokens),
        )

        try:
            summary = await self._llm_client.generate(
                prompt,
                system_prompt=_prompts.get("conversation_compressor.summarize_system.md"),
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
        model: str | None = None,
    ) -> int:
        """Estimate token count for messages.

        Uses tiktoken for accurate counting when available,
        falls back to character-based estimation otherwise.

        Args:
            messages: Messages to estimate.
            model: Model name for encoding selection.

        Returns:
            Token count (exact if tiktoken available, estimated otherwise).
        """
        # Convert to dicts for count_message_tokens
        msg_dicts = [
            {"role": m.role, "content": m.content}
            for m in messages
        ]
        return count_message_tokens(msg_dicts, model)


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
        self._current_summary: str | None = None
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

        prompt = _prompts.get(
            "conversation_compressor.update_summary.md",
            max_summary_tokens=str(self._max_summary_tokens),
            current_summary=current_summary,
            new_text=new_text,
        )

        try:
            return await self._llm_client.generate(
                prompt,
                system_prompt=_prompts.get("conversation_compressor.update_summary_system.md"),
            )
        except Exception as e:
            logger.warning("Failed to update summary: %s", e)
            # Fallback: keep current summary
            return current_summary + f"\n(+{len(new_messages)} more messages)"
