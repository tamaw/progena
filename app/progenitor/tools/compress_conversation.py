"""
Compress Conversation - Compress conversation history to reduce context window size.
"""

import logging

from ..env_utils import env_bool, env_int
from ..nodes.node_helpers.protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class CompressConversationTool(BaseSystemTool):
    """Compress conversation history to reduce context window size.

    Summarises older messages via the LLM and replaces them with a compact
    summary message, keeping the most recent messages intact.  Configurable
    via environment variables (see .env.example):

      PROGENITOR_COMPRESS_KEEP_RECENT   – recent messages to preserve (default 10)
      PROGENITOR_COMPRESS_MAX_SUMMARY_TOKENS – target summary length (default 500)
      PROGENITOR_COMPRESS_THRESHOLD     – message count before auto-flag (default 30)
      PROGENITOR_COMPRESS_PERSIST       – write result to current_state.json (default true)
    """

    @property
    def name(self) -> str:
        return "compress_conversation"

    @property
    def description(self) -> str:
        return (
            "Compress conversation history by summarising older messages "
            "to reduce context window usage"
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "compress conversation",
            "compress chat",
            "compress history",
            "summarize conversation",
            "summarise conversation",
            "compact conversation",
            "reduce context",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        from ..llm.conversation_compressor import ConversationCompressor
        from ..llm.tokenizer import count_message_tokens
        from ..state.models import ConversationMessage

        # ── Load config from env with sane defaults ──────────────────────
        keep_recent = env_int("PROGENITOR_COMPRESS_KEEP_RECENT", 10)
        max_summary_tokens = env_int("PROGENITOR_COMPRESS_MAX_SUMMARY_TOKENS", 500)
        threshold = env_int("PROGENITOR_COMPRESS_THRESHOLD", 30)
        persist = env_bool("PROGENITOR_COMPRESS_PERSIST", True)

        # ── Gather messages (prefer in-memory state) ────────────────────
        messages: list[ConversationMessage] = []

        # Use in-memory state as the authoritative source
        if context.state:
            raw = context.state.get("messages", [])
            for m in raw:
                if isinstance(m, ConversationMessage):
                    messages.append(m)
                elif isinstance(m, dict):
                    try:
                        messages.append(ConversationMessage(**m))
                    except Exception:
                        continue

        if not messages:
            return ToolResult.ok({
                "compressed": False,
                "reason": "No messages found in conversation history.",
                "message_count": 0,
            })

        original_count = len(messages)

        if original_count <= keep_recent:
            return ToolResult.ok({
                "compressed": False,
                "reason": f"Only {original_count} messages — below keep_recent ({keep_recent}). Nothing to compress.",
                "message_count": original_count,
            })

        # ── Token estimate before compression ────────────────────────────
        msg_dicts_before = [{"role": m.role, "content": m.content} for m in messages]
        tokens_before = count_message_tokens(msg_dicts_before)

        # ── Compress ─────────────────────────────────────────────────────
        compressor = ConversationCompressor(
            llm_client=context.llm_client,
            max_summary_tokens=max_summary_tokens,
        )

        try:
            compressed = await compressor.compress(messages, keep_recent=keep_recent)
        except Exception as e:
            logger.error("compress_conversation: compression failed: %s", e, exc_info=True)
            return ToolResult.fail(f"Compression failed: {e}")

        # ── Token estimate after compression ─────────────────────────────
        msg_dicts_after = [{"role": m.role, "content": m.content} for m in compressed]
        tokens_after = count_message_tokens(msg_dicts_after)

        # ── Persist if enabled ───────────────────────────────────────────
        if persist and context.state_manager:
            try:
                await context.state_manager.save_messages(compressed)
                logger.info(
                    "compress_conversation: persisted %d → %d messages (%d → %d tokens)",
                    original_count, len(compressed), tokens_before, tokens_after,
                )
            except Exception as e:
                logger.warning("compress_conversation: failed to persist: %s", e)

        return ToolResult.ok({
            "compressed": True,
            "original_message_count": original_count,
            "compressed_message_count": len(compressed),
            "messages_summarised": original_count - len(compressed) + 1,  # +1 for summary msg
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "tokens_saved": tokens_before - tokens_after,
            "compression_ratio": round(tokens_after / tokens_before, 2) if tokens_before else 1.0,
            "keep_recent": keep_recent,
            "threshold": threshold,
            "persisted": persist and context.state_manager is not None,
        }, state_updates={"messages": compressed})
