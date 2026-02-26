"""
Spawn GitHub Agent tool.

Dispatches a GitHub Actions workflow via GH CLI and returns run metadata so
the user can review progress/outputs from chat channels (including Telegram).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, cast

from .gh import _run_gh
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_WORKFLOW = "agents.yml"
_DEFAULT_REF = "main"
_DEFAULT_REQUEST_INPUT = "request"


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _return_code(result: dict[str, Any]) -> int:
    try:
        return int(result.get("returncode", -1))
    except Exception:
        return -1


def _coerce_inputs(raw_inputs: Any) -> dict[str, str]:
    if isinstance(raw_inputs, dict):
        return {
            str(k).strip(): str(v).strip()
            for k, v in raw_inputs.items()
            if str(k).strip() and str(v).strip()
        }
    if isinstance(raw_inputs, str):
        with_inputs = raw_inputs.strip()
        if not with_inputs:
            return {}
        try:
            parsed = json.loads(with_inputs)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return {
                str(k).strip(): str(v).strip()
                for k, v in parsed.items()
                if str(k).strip() and str(v).strip()
            }
    return {}


class SpawnGithubAgentTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "spawn_github_agent"

    @property
    def description(self) -> str:
        return (
            "Dispatch a GitHub Actions workflow to spawn a GitHub agent and return "
            "run status metadata for review"
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        request_text = _safe_text(args.get("request") or context.user_input)
        workflow = _safe_text(args.get("workflow")) or os.getenv(
            "PROGENITOR_GH_AGENT_WORKFLOW",
            _DEFAULT_WORKFLOW,
        )
        ref = _safe_text(args.get("ref")) or os.getenv(
            "PROGENITOR_GH_AGENT_REF",
            _DEFAULT_REF,
        )
        request_input_name = os.getenv(
            "PROGENITOR_GH_AGENT_REQUEST_INPUT",
            _DEFAULT_REQUEST_INPUT,
        ).strip() or _DEFAULT_REQUEST_INPUT
        run_event = _safe_text(args.get("event")) or "workflow_dispatch"
        cwd = Path(os.getenv("PROGENITOR_GH_AGENT_CWD", ".")).resolve()

        if not request_text:
            return ToolResult.fail("Missing request text for github agent dispatch.")
        if not workflow:
            return ToolResult.fail("Missing workflow name for github agent dispatch.")
        if not cwd.exists() or not cwd.is_dir():
            return ToolResult.fail(f"Invalid dispatch working directory: {cwd}")

        # Quick auth check before dispatching.
        auth_check = await _run_gh(["auth", "status"], cwd=cwd, timeout=20.0)
        if _return_code(auth_check) != 0:
            return ToolResult.fail(
                "GH CLI is not authenticated. Set GH_TOKEN/GITHUB_TOKEN or run `gh auth login`. "
                + _safe_text(auth_check.get("stderr"))
            )

        inputs = _coerce_inputs(args.get("inputs"))
        if request_input_name not in inputs:
            inputs[request_input_name] = request_text
        if "mode" not in inputs and workflow.endswith("agents.yml"):
            # Keep existing agents workflow behavior sane by default.
            inputs["mode"] = "changed"

        dispatch_args = ["workflow", "run", workflow, "--ref", ref]
        for key, value in inputs.items():
            dispatch_args.extend(["-f", f"{key}={value}"])

        dispatch = await _run_gh(dispatch_args, cwd=cwd, timeout=60.0)
        if _return_code(dispatch) != 0:
            return ToolResult.fail(
                "Failed to dispatch GitHub workflow. " + _safe_text(dispatch.get("stderr"))
            )

        # Pull latest run metadata for immediate user feedback.
        run_list_args = [
            "run", "list",
            "--workflow", workflow,
            "--event", run_event,
            "--limit", "1",
            "--json", "databaseId,url,status,conclusion,workflowName,displayTitle,headBranch,createdAt",
        ]
        if ref:
            run_list_args.extend(["--branch", ref])
        run_list = await _run_gh(run_list_args, cwd=cwd, timeout=30.0)

        latest_run: dict[str, Any] | None = None
        if _return_code(run_list) == 0:
            try:
                parsed = json.loads(_safe_text(run_list.get("stdout")) or "[]")
                if isinstance(parsed, list) and parsed:
                    latest_run = cast(dict[str, Any], parsed[0])
            except json.JSONDecodeError:
                logger.warning("spawn_github_agent: failed to parse gh run list JSON")

        return ToolResult.ok(
            {
                "dispatched": True,
                "workflow": workflow,
                "ref": ref,
                "inputs": inputs,
                "request": request_text,
                "cwd": str(cwd),
                "dispatch_stdout": _safe_text(dispatch.get("stdout")),
                "latest_run": latest_run,
            }
        )
