"""Workflow domain; lazy model aliases support historical LangGraph checkpoints.

Application code imports contracts and capabilities from their owning modules.
"""

from importlib import import_module

_CHECKPOINT_MODELS = frozenset(
    {
        "DocumentQueryFilter",
        "WorkflowNodeEvent",
        "WorkflowNodeCron",
        "WorkflowNodeData",
        "WorkflowNodeDocumentRead",
        "WorkflowNodeDocumentQuery",
        "WorkflowNodeDocumentWrite",
        "WorkflowNodeDocumentCreate",
        "WorkflowNodeDocumentDelete",
        "WorkflowNodeUnpack",
        "WorkflowNodeFormat",
        "WorkflowNodeExtract",
        "WorkflowNodeAgent",
        "WorkflowNodeRoutine",
        "WorkflowNodeCondition",
        "WorkflowNodeApproval",
        "WorkflowEdgeEndpoint",
        "WorkflowEdge",
        "WorkflowGraph",
        "WorkflowDefinition",
        "WorkflowListItem",
        "WorkflowRun",
        "WorkflowInvokeRequest",
    }
)


def __getattr__(name: str):
    if name not in _CHECKPOINT_MODELS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module("src.progenitor.workflows.models"), name)
