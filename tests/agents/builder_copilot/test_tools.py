import json

from app.agents.builder_copilot.tools import _derive_work_item_suffix


def test_derive_work_item_suffix_prefers_a2a_task_id() -> None:
    context = {"a2a_task_id": "9e286e23-0264-4dad-a43f-7619c41053d3"}
    suffix = _derive_work_item_suffix(json.dumps(context))
    assert suffix.startswith("9e286e2302644dad")


def test_derive_work_item_suffix_fallback_is_short_uuid() -> None:
    suffix = _derive_work_item_suffix(None)
    assert len(suffix) == 8
