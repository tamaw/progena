"""Custom middleware exports for the Progenitor DeepAgent."""

from app.progenitor.ipc.stream_events import emit_stream_event

from .async_subagent_tracking import AsyncSubagentTrackingMiddleware
from .audit_logging import AuditLoggingMiddleware
from .direct_a2a_fast_path import DirectA2AFastPathMiddleware
from .dynamic_mcp import DynamicMCPToolMiddleware
from .metrics import MetricsMiddleware
from .schema_compaction import SchemaCompactionMiddleware
from .secret_redaction import SecretRedactionMiddleware

__all__ = [
    "AsyncSubagentTrackingMiddleware",
    "AuditLoggingMiddleware",
    "DirectA2AFastPathMiddleware",
    "DynamicMCPToolMiddleware",
    "emit_stream_event",
    "MetricsMiddleware",
    "SchemaCompactionMiddleware",
    "SecretRedactionMiddleware",
]
