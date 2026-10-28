"""Custom middleware exports for the Progena DeepAgent."""

from src.progena.agent.clock_context import ClockContextMiddleware
from src.progena.agent.middleware.direct_a2a_fast_path import DirectA2AFastPathMiddleware
from src.progena.runtime.streaming.stream_events import emit_stream_event

from .async_subagent_tracking import AsyncSubagentTrackingMiddleware
from .dynamic_mcp import DynamicMCPToolMiddleware
from .live_instruction import LiveInstructionMiddleware
from .schema_compaction import SchemaCompactionMiddleware
from .secret_redaction import SecretRedactionMiddleware
from .tool_errors import ToolErrorMiddleware
from .tool_result_pairing import ToolResultPairingMiddleware
from .tool_timing import ToolTimingMiddleware

__all__ = [
    "AsyncSubagentTrackingMiddleware",
    "ClockContextMiddleware",
    "ToolTimingMiddleware",
    "DirectA2AFastPathMiddleware",
    "DynamicMCPToolMiddleware",
    "emit_stream_event",
    "LiveInstructionMiddleware",
    "SchemaCompactionMiddleware",
    "SecretRedactionMiddleware",
    "ToolErrorMiddleware",
    "ToolResultPairingMiddleware",
]
