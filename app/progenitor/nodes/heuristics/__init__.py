"""
Heuristics module for intent detection and parameter extraction.

These fast-path heuristics avoid LLM calls for common, unambiguous requests.
"""

from .intent_detection import (
    looks_like_agent_build_request,
    looks_like_agent_discovery_request,
    looks_like_list_tools_request,
    looks_like_build_status_request,
    looks_like_current_time_request,
    looks_like_schedule_request,
    looks_like_list_schedule_request,
    looks_like_self_reflect_request,
)

from .parameter_extraction import (
    extract_schedule_params,
    infer_target_capabilities,
    extract_project_name_from_input,
    extract_meaningful_words,
)

__all__ = [
    # Intent detection
    "looks_like_agent_build_request",
    "looks_like_agent_discovery_request",
    "looks_like_list_tools_request",
    "looks_like_build_status_request",
    "looks_like_current_time_request",
    "looks_like_schedule_request",
    "looks_like_list_schedule_request",
    "looks_like_self_reflect_request",
    # Parameter extraction
    "extract_schedule_params",
    "infer_target_capabilities",
    "extract_project_name_from_input",
    "extract_meaningful_words",
]
