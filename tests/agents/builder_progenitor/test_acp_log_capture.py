"""Tests for ACP log file capture."""

from app.agents.builder_progenitor.copilot_acp import ACPLogCapture


def test_acp_log_capture_writes_split_stdout_stderr_logs(tmp_path):
    capture = ACPLogCapture(tmp_path)
    result = {
        "success": False,
        "stop_reason": "error",
        "elapsed": 1.23,
        "stdout": "acp stdout line",
        "stderr": "acp stderr line",
        "tool_calls": [],
    }

    summary_path = capture.write_log("work-item-1", result)

    assert summary_path.exists()
    assert summary_path.name.endswith(".log")
    assert summary_path.with_suffix(".stdout.log").read_text(encoding="utf-8") == "acp stdout line"
    assert summary_path.with_suffix(".stderr.log").read_text(encoding="utf-8") == "acp stderr line"


def test_acp_log_capture_recreates_missing_directory(tmp_path):
    capture = ACPLogCapture(tmp_path)
    capture.log_dir.rmdir()

    summary_path = capture.write_log("work-item-2", {"stdout": "", "stderr": "", "success": True, "elapsed": 0.1})

    assert summary_path.exists()
