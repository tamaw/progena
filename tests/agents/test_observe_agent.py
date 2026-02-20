import json
from pathlib import Path

import pytest

from app.agents.observe_agent.agent import ObserveAgent


@pytest.mark.asyncio
async def test_observe_agent_reports_health_and_errors(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    logs_dir = tmp_path / "logs"
    state_dir.mkdir()
    logs_dir.mkdir()

    (state_dir / "health.json").write_text(json.dumps({"status": "error", "error": "boom", "cycle_count": 12}))
    (state_dir / "current_state.json").write_text(json.dumps({"run_id": "run_123", "cycle_count": 12, "messages": []}))
    (state_dir / "build_state.json").write_text(json.dumps({"build_in_progress": False, "backlog": []}))
    (state_dir / "registry.json").write_text(json.dumps({"agents": [], "tools": []}))

    (logs_dir / "decisions.jsonl").write_text(
        json.dumps({"event": "planning_error", "error": "something broke"}) + "\n"
    )
    (logs_dir / "schedule.jsonl").write_text("")
    (logs_dir / "conversation.jsonl").write_text("")
    (logs_dir / "progenitor.client.stderr.log").write_text("Traceback: ...\nValueError: nope\n")

    agent = ObserveAgent(state_dir=state_dir, logs_dir=logs_dir)
    result = await agent.process("why did you error last time", {})

    report = result["report"]
    assert "focus: errors" in report.lower()
    assert "- status: error" in report.lower()
    assert "recent error signals" in report.lower()
