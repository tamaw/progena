import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("PROGENITOR_DATA_DIR", str(Path(__file__).resolve().parents[3] / "data"))

from app.agents.builder_agent.nodes.verify import create_node
from app.agents.builder_agent.routing import route_after_verify, should_continue_inner_loop


@pytest.mark.asyncio
async def test_verify_marks_missing_inputs_as_not_run(monkeypatch):
    state_manager = AsyncMock()
    node = create_node(llm_client=AsyncMock(), state_manager=state_manager)
    monkeypatch.setattr(
        "app.agents.builder_agent.nodes.verify.get_ideal_state",
        lambda _state: {"task_description": "Build something useful"},
    )

    result = await node(
        {
            "iteration": {"current": 2},
            "phases": {},
            "history": {},
            "best": {},
            "target_score": 0.8,
        }
    )

    assert result["phases"]["verify"]["status"] == "not_run"
    assert result["phases"]["verify"]["score"] == 0.0
    assert result["verification"]["current_status"] == "not_run"
    assert result["verification"]["current_passed"] is False
    assert result["verification"]["current_report"].status == "not_run"


def test_route_after_verify_distinguishes_not_run_failed_and_passed():
    assert (
        route_after_verify(
            {
                "iteration": {"current": 1, "max": 3},
                "verification": {
                    "current_status": "not_run",
                    "current_score": 0.0,
                    "target_score": 0.8,
                },
            }
        )
        == "done"
    )

    assert (
        route_after_verify(
            {
                "iteration": {"current": 1, "max": 3},
                "verification": {
                    "current_status": "failed",
                    "current_score": 0.4,
                    "target_score": 0.8,
                },
            }
        )
        == "learn"
    )

    assert (
        route_after_verify(
            {
                "iteration": {"current": 1, "max": 3},
                "verification": {
                    "current_status": "passed",
                    "current_score": 0.9,
                    "target_score": 0.8,
                },
            }
        )
        == "publish"
    )


def test_route_after_verify_handles_non_dict_iteration_section():
    assert (
        route_after_verify(
            {
                "iteration": "invalid",
                "verification": {
                    "current_status": "failed",
                    "current_score": 0.4,
                    "target_score": 0.8,
                },
            }
        )
        == "learn"
    )


def test_route_after_verify_preserves_explicit_zero_max_iterations():
    assert (
        route_after_verify(
            {
                "iteration": {"current": 0, "max": 0},
                "verification": {
                    "current_status": "failed",
                    "current_score": 0.0,
                    "target_score": 0.8,
                },
            }
        )
        == "publish"
    )


def test_should_continue_inner_loop_handles_non_dict_sections():
    assert (
        should_continue_inner_loop(
            {
                "iteration": "invalid",
                "verification": "invalid",
            }
        )
        == "observe"
    )
