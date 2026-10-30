"""Bridge Telegram chats to the Progena runtime."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from typing import Any

import src.backend.channels.telegram.client as telegram_client
import src.backend.chat.responses as chat_responses
import src.backend.integrations.runtime as integrations_runtime
import src.backend.integrations.subprocess as integrations_subprocess
import src.backend.storage.documents as storage_documents
from src.backend.http.contracts import SOURCE_TELEGRAM
from src.progena.documents.kinds import SESSION_KEY_PREFIX

logger = logging.getLogger(__name__)


def _telegram_conversation_id(chat_id: Any, message_thread_id: Any = None) -> str:
    """Runtime conversation for a Telegram chat (and forum topic, when used).

    Telegram turns persist per chat under a ``tele_`` prefix; other callers
    that name no conversation get an isolated ``conv_`` one from the runtime.
    """
    conversation_id = f"tele_{chat_id}"
    if message_thread_id is not None:
        conversation_id += f"_{message_thread_id}"
    return conversation_id


async def _send_telegram_connected_message(
    telegram: telegram_client.TelegramClient,
    progena: integrations_subprocess.ProgenaClient | integrations_runtime.SSEProgenaClient,
    document_store: storage_documents.BackendDocumentStore,
) -> None:
    """Trigger an agent-generated greeting the first time Telegram connects.

    This routes through the same backend -> Progena SSE request/response
    path as a normal user message, but is auto-invoked on connection. A chat
    that already has a session document has been greeted, so restarts stay quiet.
    """
    conversation_id = _telegram_conversation_id(telegram.config.chat_id)
    session = await asyncio.to_thread(
        document_store.get_document,
        storage_documents.PROGENA_STATE_PREFIX,
        f"{SESSION_KEY_PREFIX}{conversation_id}",
    )
    if session is not None:
        logger.info("Telegram chat %s already has a session; skipping greeting", conversation_id)
        return

    prompt = (
        "Hi! I just connected to you on Telegram. "
        "Please reply with a short, friendly greeting and invite me to send a message."
    )
    request_id = f"telegram-connect-{uuid.uuid4().hex[:8]}"

    try:
        # Ensure the client is started/connected (we normally lazy-start).
        if not progena.is_running:
            await progena.start()
        response_text = (
            await progena.send_message(
                prompt,
                request_id=request_id,
                metadata={"thread_id": conversation_id, "source": SOURCE_TELEGRAM},
            )
        ).response.strip()
        if not response_text:
            raise RuntimeError("Empty greeting response from agent")

        sent = await telegram.send_message(response_text)
        if not sent:
            logger.warning("Telegram greeting send returned False")
    except Exception as e:
        logger.warning("Failed to send agent-generated Telegram greeting: %s", e)

        # Fallback should signal failure (if the agent path failed, we're not fully connected).
        try:
            sent = await telegram.send_message(
                "⚠️ Progena failed to connect to the agent. "
                "I can't respond yet — please check the backend/agent logs and try again."
            )
            if not sent:
                logger.warning("Fallback Telegram failure message send returned False")
        except Exception as e2:
            logger.warning("Failed to send fallback Telegram failure message: %s", e2)


async def _handle_telegram_agent_message(
    telegram: telegram_client.TelegramClient,
    progena: integrations_subprocess.ProgenaClient | integrations_runtime.SSEProgenaClient,
    chat_id: str,
    message: str,
    metadata: dict[str, Any],
) -> str | None:
    """Handle an inbound Telegram chat by streaming the reply back to Telegram."""
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            logger.info(
                "Processing Telegram message from %s (attempt %d): %s",
                metadata.get("username", chat_id),
                attempt + 1,
                message[:50] + "..." if len(message) > 50 else message,
            )

            if not progena.is_running:
                logger.info("SSE client not running, starting...")
                await progena.start()

            request_id = f"telegram-{chat_id}-{uuid.uuid4().hex[:8]}"
            conversation_id = _telegram_conversation_id(chat_id, metadata.get("message_thread_id"))
            streamed_chars = await telegram.stream_agent_response_to_chat(
                chat_id,
                chat_responses._stream_text_chunks(
                    progena.send_message_stream_events(
                        message,
                        request_id=request_id,
                        metadata={
                            "thread_id": conversation_id,
                            "source": SOURCE_TELEGRAM,
                        },
                    )
                ),
                message_thread_id=metadata.get("message_thread_id"),
            )

            logger.info(
                "Streamed Telegram response for %s (%d chars)",
                metadata.get("username", chat_id),
                streamed_chars,
            )
            if streamed_chars == 0:
                return "⚠️ Empty response from agent."
            return None

        except telegram_client.TelegramDraftStreamingError as e:
            logger.error("Telegram draft streaming failed: %s", e)
            return (
                "⚠️ Telegram native draft streaming failed. "
                "Please check the backend logs and bot API support."
            )
        except TimeoutError:
            logger.error("Timeout waiting for agent response")
            return "⏱️ Sorry, the request timed out. Please try again."
        except (ConnectionError, OSError) as e:
            logger.warning("Connection error on attempt %d: %s", attempt + 1, e)
            if attempt < max_retries:
                with suppress(Exception):
                    await progena.stop()
                await asyncio.sleep(1)
                continue
            logger.error("Failed after %d attempts: %s", max_retries + 1, e)
            return "⚠️ Connection error. Please try again in a moment."
        except Exception as e:
            logger.error("Error processing Telegram message: %s", e)
            return None

    return None
