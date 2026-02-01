"""
Backend - FastAPI gateway to Progenitor.

Provides an OpenAI-compatible chat interface that communicates
with the Progenitor agent via stdin/stdout subprocess.

Enhanced features (from text-generation-webui patterns):
- Streaming callbacks for real-time token streaming
- Conversation metadata with reasoning traces
- File attachments for document-grounded chat
- Session management for state persistence
- Graceful shutdown with request draining
"""

__version__ = "0.2.0"

# Core exports
from .main import app, config

# Enhanced feature modules
from .streaming import (
    Iteratorize,
    AsyncIteratorize,
    StreamingBuffer,
    stream_with_callback,
    stop_everything,
    reset_stop_flag,
    StopNowException,
)
from .conversation import (
    ConversationHistory,
    ConversationMessage,
    MessageMetadata,
    AttachmentType,
    Attachment,
    ToolCallRecord,
    ReasoningStep,
    truncate_content_by_tokens,
)
from .attachments import (
    AttachmentManager,
    AttachmentConfig,
    AttachmentResult,
    format_attachments_for_context,
    create_attachment_from_upload,
)
from .session import (
    SessionManager,
    SessionConfig,
    SessionState,
    UserPreferences,
    SettingsManager,
)
from .shutdown import (
    GracefulShutdownManager,
    ShutdownPhase,
    managed_shutdown,
    stop_client_safely,
    commit_state_safely,
)

__all__ = [
    # Core
    "app",
    "config",
    # Streaming
    "Iteratorize",
    "AsyncIteratorize",
    "StreamingBuffer",
    "stream_with_callback",
    "stop_everything",
    "reset_stop_flag",
    "StopNowException",
    # Conversation
    "ConversationHistory",
    "ConversationMessage",
    "MessageMetadata",
    "AttachmentType",
    "Attachment",
    "ToolCallRecord",
    "ReasoningStep",
    "truncate_content_by_tokens",
    # Attachments
    "AttachmentManager",
    "AttachmentConfig",
    "AttachmentResult",
    "format_attachments_for_context",
    "create_attachment_from_upload",
    # Session
    "SessionManager",
    "SessionConfig",
    "SessionState",
    "UserPreferences",
    "SettingsManager",
    # Shutdown
    "GracefulShutdownManager",
    "ShutdownPhase",
    "managed_shutdown",
    "stop_client_safely",
    "commit_state_safely",
]
