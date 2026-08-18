from datetime import UTC, datetime

import pytest

from app.progenitor.schedule.dispatch import enqueue_due_schedule_entries


class _StateManager:
    def __init__(self, entry):
        self.entry = entry
        self.workflow_requests = []
        self.runs = []
        self.entry_updates = []
        self.events = []

    async def get_schedule(self):
        return {self.entry["id"]: self.entry}

    async def request_workflow_run(self, **kwargs):
        self.workflow_requests.append(kwargs)

    async def upsert_schedule_run(self, run):
        self.runs.append(run)

    async def update_schedule_entry(self, schedule_id, updates):
        self.entry_updates.append((schedule_id, updates))

    async def log_schedule_event(self, event):
        self.events.append(event)


class _Queues:
    def __init__(self):
        self.inputs = []
        self.events = []

    async def enqueue_input(self, *args, **kwargs):
        self.inputs.append((args, kwargs))
        return True

    async def push_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


@pytest.mark.asyncio
async def test_due_schedule_workflow_writes_durable_invoke_request(monkeypatch):
    entry = {
        "id": "sched_workflow",
        "message": "Run the release review.",
        "workflow_id": "release_review",
        "cron_expression": "0 * * * *",
    }
    state_manager = _StateManager(entry)
    queues = _Queues()

    monkeypatch.setattr(
        "app.progenitor.schedule.dispatch.Scheduler.get_due_items",
        lambda _self, _entries, _now: [entry],
    )

    count = await enqueue_due_schedule_entries(
        state_manager, queues, current_time=datetime.now(UTC)
    )

    assert count == 1
    assert queues.inputs == []
    assert state_manager.workflow_requests[0]["workflow_id"] == "release_review"
    assert state_manager.workflow_requests[0]["input"]["schedule_id"] == "sched_workflow"
