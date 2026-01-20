"""
Chat Server Main - FastAPI application with OpenAI-compatible interface.

This provides a standardized chat API that proxies to the Progenitor agent.
"""

import asyncio
import logging
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from .config import ChatServerConfig
from .client import ProgenitorClient, get_client
from .models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatMessage,
    Usage,
    ModelsResponse,
    ModelInfo,
    ErrorResponse,
    ErrorDetail,
    ResponseRequest,
    ResponsePayload,
    ResponseOutputMessage,
    ResponseOutputText,
)

logger = logging.getLogger(__name__)

# Global config and client
config = ChatServerConfig.from_env()
client: ProgenitorClient = None  # type: ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global client

    # Startup
    logger.info("Starting chat server...")
    client = get_client(config)

    # Don't auto-start Progenitor - let it start on first request
    # This allows the server to start even if Progenitor has issues
    logger.info(f"Chat server ready on {config.host}:{config.port}")

    yield

    # Shutdown
    logger.info("Shutting down chat server...")
    if client:
        await client.stop()


app = FastAPI(
    title=config.api_title,
    version=config.api_version,
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for API errors."""
    logger.error(f"API error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorDetail(
                message=str(exc),
                type="internal_error",
            )
        ).model_dump(),
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "progenitor_running": client.is_running if client else False,
    }


@app.get("/v1/models", response_model=ModelsResponse)
async def list_models():
    """List available models (OpenAI-compatible)."""
    return ModelsResponse(
        data=[
            ModelInfo(
                id=config.model_name,
                created=int(time.time()),
                owned_by="progenitor",
            )
        ]
    )


@app.post("/v1/responses", response_model=ResponsePayload)
async def responses(request: ResponseRequest):
    """Unified Responses API (preferred).

    Accepts either the new Responses `input` shape or the legacy
    Chat Completions `messages` for compatibility.
    """
    if not client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    try:
        last_user_msg, all_inputs = _extract_response_input(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        if request.stream:
            return StreamingResponse(
                stream_response_unified(last_user_msg, request.model),
                media_type="text/event-stream",
            )

        response_text = await client.send_message(last_user_msg)
        response_id = f"resp-{uuid.uuid4().hex[:8]}"

        return ResponsePayload(
            id=response_id,
            model=request.model,
            output=[
                ResponseOutputMessage(
                    content=[ResponseOutputText(text=response_text)]
                )
            ],
            usage=Usage(
                prompt_tokens=len(" ".join(all_inputs).split()),
                completion_tokens=len(response_text.split()),
                total_tokens=len(" ".join(all_inputs).split()) + len(response_text.split()),
            ),
        )

    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"Responses API error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    """Legacy Chat Completions API.

    Kept for backwards compatibility but internally powered by
    the same engine as /v1/responses.
    """
    if not client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    try:
        last_user_msg = _extract_chat_message(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        if request.stream:
            return StreamingResponse(
                stream_response(last_user_msg, request.model),
                media_type="text/event-stream",
            )

        response_text = await client.send_message(last_user_msg)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

        return ChatCompletionResponse(
            id=completion_id,
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=response_text,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=len(last_user_msg.split()),
                completion_tokens=len(response_text.split()),
                total_tokens=len(last_user_msg.split()) + len(response_text.split()),
            ),
        )

    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"Chat completion error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def stream_response(message: str, model: str) -> AsyncIterator[str]:
    """Stream response chunks in SSE format."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    try:
        async for chunk in client.send_message_stream(message):
            chunk_data = ChatCompletionChunk(
                id=completion_id,
                model=model,
                choices=[
                    {
                        "index": 0,
                        "delta": {"content": chunk},
                        "finish_reason": None,
                    }
                ],
            )
            yield f"data: {chunk_data.model_dump_json()}\n\n"

        # Send final chunk with finish_reason
        final_chunk = ChatCompletionChunk(
            id=completion_id,
            model=model,
            choices=[
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        )
        yield f"data: {final_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"Streaming error: {e}", exc_info=True)
        error_chunk = {"error": str(e)}
        yield f"data: {error_chunk}\n\n"


def _extract_response_input(request: ResponseRequest) -> tuple[str, list[str]]:
    """Normalize Responses API input to a single user string."""
    inputs: list[str] = []

    raw_input = request.input
    if isinstance(raw_input, str):
        inputs.append(raw_input)
    else:
        for item in raw_input:
            if isinstance(item, str):
                inputs.append(item)
            else:
                inputs.append(item.content)

    if not inputs and request.messages:
        inputs.extend([msg.content for msg in request.messages if msg.role in ("user", "system")])

    if not inputs:
        raise ValueError("No input provided")

    return inputs[-1], inputs


def _extract_chat_message(request: ChatCompletionRequest) -> str:
    """Extract last user message from Chat Completions request."""
    if not request.messages:
        raise ValueError("No messages provided")

    user_messages = [
        f"{msg.role}: {msg.content}"
        for msg in request.messages
        if msg.role in ("user", "system")
    ]

    if not user_messages:
        raise ValueError("No user message found")

    return next(
        (msg.content for msg in reversed(request.messages) if msg.role == "user"),
        user_messages[-1],
    )


async def stream_response_unified(message: str, model: str) -> AsyncIterator[str]:
    """Stream SSE events using the Responses API shape."""
    response_id = f"resp-{uuid.uuid4().hex[:8]}"

    try:
        async for chunk in client.send_message_stream(message):
            delta_event = {
                "id": response_id,
                "object": "response.output_text.delta",
                "model": model,
                "output_index": 0,
                "delta": {
                    "type": "output_text.delta",
                    "text": chunk,
                },
            }
            yield "event: response.output_text.delta\n" + f"data: {json.dumps(delta_event)}\n\n"

        completed_event = {
            "id": response_id,
            "object": "response.completed",
            "model": model,
        }
        yield "event: response.completed\n" + f"data: {json.dumps(completed_event)}\n\n"

    except Exception as e:
        logger.error(f"Streaming error: {e}", exc_info=True)
        error_event = {"error": str(e)}
        yield "event: error\n" + f"data: {json.dumps(error_event)}\n\n"


# Simple chat endpoint (non-OpenAI format)
@app.post("/chat")
async def simple_chat(request: dict):
    """Simple chat endpoint for quick testing.

    Request: {"message": "your message here"}
    Response: {"response": "progenitor's response"}
    """
    if not client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    message = request.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="No message provided")

    try:
        response = await client.send_message(message)
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
