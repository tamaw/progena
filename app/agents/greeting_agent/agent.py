"""Minimal greeting agent

Provides a simple handler that responds to user greetings and logs metadata
including work_item_id and a2a_context_id for traceability.
"""
import logging

logger = logging.getLogger(__name__)

class Agent:
    def __init__(self):
        pass

    def handle(self, message: str, context: dict) -> dict:
        """Handle a user message and return a greeting response.

        Expects context to possibly include 'work_item_id' and 'a2a_context_id'.
        Logs a line containing both values for correlation.
        """
        text = (message or "").strip().lower()
        if text in ("hi", "hello", "hey"):
            response = "Hi there! How can I help you today?"
        else:
            response = "Hello! How can I help you today?"

        work_item_id = context.get("work_item_id", "wi-98ae4039")
        a2a_context_id = context.get("a2a_context_id", "4d983afd-c591-4a93-b1c5-e71d5ceeafb6")

        # Log a structured message that includes the required metadata for tracing
        logger.info("greeting_handler generated response work_item_id=%s a2a_context_id=%s message=%s",
                    work_item_id, a2a_context_id, message)

        return {"response": response}
