"""Tests for ACP log file capture behavior."""

from app.agents.builder_agent.runtime.copilot_acp import ACPLogCapture


def test_acp_log_capture_writes_split_stdout_stderr_logs(tmp_path):
    capture = ACPLogCapture(tmp_path)
    result = {
        "success": False,
        "stop_reason": "error",
        "elapsed_seconds": 1.23,
        "stdout": "acp stdout line",
        "stderr": "acp stderr line",
        "chunks": [],
        "tool_calls": [],
    }

    summary_path = capture.write_log("work-item-1", result)

    assert summary_path.exists()
    assert summary_path.name.endswith(".log")
    assert summary_path.with_suffix(".stdout.log").read_text(encoding="utf-8") == "acp stdout line"
    assert summary_path.with_suffix(".stderr.log").read_text(encoding="utf-8") == "acp stderr line"
