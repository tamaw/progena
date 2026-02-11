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
from .attachments import (
    AttachmentConfig,
    AttachmentManager,
    AttachmentResult,
    create_attachment_from_upload,
    format_attachments_for_context,
)
from .conversation import (
    Attachment,
    AttachmentType,
    ConversationHistory,
    ConversationMessage,
    MessageMetadata,
    ReasoningStep,
    ToolCallRecord,
    truncate_content_by_tokens,
)
from .main import app, config
from .session import (
    SessionConfig,
    SessionManager,
    SessionState,
    SettingsManager,
    UserPreferences,
)
from .shutdown import (
    GracefulShutdownManager,
    ShutdownPhase,
    commit_state_safely,
    managed_shutdown,
    stop_client_safely,
)

# Enhanced feature modules
from .streaming import (
    AsyncIteratorize,
    Iteratorize,
    StopNowException,
    StreamingBuffer,
    reset_stop_flag,
    stop_everything,
    stream_with_callback,
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
