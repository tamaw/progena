from pathlib import Path

from app.agents.observe_agent.config import ObserveConfig
from app.agents.observe_agent.prompts import load_prompt
from app.agents.observe_agent.queue_worker import (
    TaskRecord,
    append_task_record,
    sync_wait_for_completion,
)


def test_observe_config_prefers_agent_model(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_DEFAULT", "gpt-5-mini")
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.1")
    monkeypatch.setenv("PROGENITOR_LOG_DIR", "/tmp/logs")
    config = ObserveConfig.load()

    assert config.llm.get_model() == "gpt-5-mini"
    assert config.storage.log_dir == Path("/tmp/logs")


def test_load_prompt_reads_observe_system_prompt() -> None:
    content = load_prompt("system.md")
    assert "You are Observe Agent" in content


def test_append_task_record_can_be_polled(tmp_path: Path) -> None:
    log_path = tmp_path / "observe_tasks.jsonl"
    append_task_record(
        TaskRecord(
            task_id="task-123",
            context_id="ctx-123",
            status="success",
            response_text="done",
            error=None,
            timestamp_utc="2026-03-30T00:00:00Z",
        ),
        log_path=log_path,
    )

    record = sync_wait_for_completion("task-123", timeout_seconds=0.1, poll_interval=0.01, log_path=log_path)
    assert record is not None
    assert record["status"] == "success"
    assert record["response_text"] == "done"
