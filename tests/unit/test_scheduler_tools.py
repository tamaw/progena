"""
Unit tests for scheduler lifecycle management tools.

Covers:
- Input validation (missing / blank schedule_id, empty updates dict, protected fields)
- State-manager client calls (via AsyncMock)
- Error handling when entries are not found
- Tool-spec shape validation for all four new specs
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.tools.schedule import (
    DELETE_SCHEDULE_TOOL_SPEC,
    PAUSE_SCHEDULE_TOOL_SPEC,
    RESUME_SCHEDULE_TOOL_SPEC,
    UPDATE_SCHEDULE_TOOL_SPEC,
    delete_scheduled_task,
    pause_scheduled_task,
    resume_scheduled_task,
    update_scheduled_task,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_sm(*, remove_return: bool = True, update_return: bool = True) -> AsyncMock:
    """Build a minimal AsyncMock that mimics the StateManager schedule API."""
    sm = AsyncMock()
    sm.remove_schedule_entry = AsyncMock(return_value=remove_return)
    sm.update_schedule_entry = AsyncMock(return_value=update_return)
    return sm


# ===========================================================================
# delete_scheduled_task
# ===========================================================================


class TestDeleteScheduledTask:

    @pytest.mark.asyncio
    async def test_delete_blank_id_returns_error(self):
        """Blank schedule_id must be rejected without touching state_manager."""
        result = await delete_scheduled_task("   ")
        assert result.success is False
        assert "required" in result.message.lower()

    @pytest.mark.asyncio
    async def test_delete_empty_id_returns_error(self):
        result = await delete_scheduled_task("")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_delete_without_state_manager_succeeds(self):
        """Without a state_manager the function reports success (intent only)."""
        result = await delete_scheduled_task("sched_abc123")
        assert result.success is True
        assert result.schedule_id == "sched_abc123"
        assert "deleted" in result.message.lower()

    @pytest.mark.asyncio
    async def test_delete_calls_state_manager_remove(self):
        """remove_schedule_entry must be called with the correct ID."""
        sm = _mock_sm(remove_return=True)
        result = await delete_scheduled_task("sched_abc123", state_manager=sm)
        sm.remove_schedule_entry.assert_awaited_once_with("sched_abc123")
        assert result.success is True
        assert result.schedule_id == "sched_abc123"

    @pytest.mark.asyncio
    async def test_delete_not_found_returns_error(self):
        """If remove_schedule_entry returns False the result must be failure."""
        sm = _mock_sm(remove_return=False)
        result = await delete_scheduled_task("sched_missing", state_manager=sm)
        assert result.success is False
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_delete_state_manager_exception_propagates(self):
        """Exceptions from state_manager bubble up (caller decides how to handle)."""
        sm = AsyncMock()
        sm.remove_schedule_entry = AsyncMock(side_effect=RuntimeError("disk error"))
        with pytest.raises(RuntimeError, match="disk error"):
            await delete_scheduled_task("sched_x", state_manager=sm)


# ===========================================================================
# update_scheduled_task
# ===========================================================================


class TestUpdateScheduledTask:

    @pytest.mark.asyncio
    async def test_update_blank_id_returns_error(self):
        result = await update_scheduled_task("", {"message": "new"})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_update_empty_updates_returns_error(self):
        result = await update_scheduled_task("sched_abc123", {})
        assert result.success is False
        assert "empty" in result.message.lower()

    @pytest.mark.asyncio
    async def test_update_protected_field_id_rejected(self):
        result = await update_scheduled_task(
            "sched_abc123", {"id": "sched_new_id"}
        )
        assert result.success is False
        assert "protected" in result.message.lower()
        assert "id" in result.message

    @pytest.mark.asyncio
    async def test_update_protected_field_created_at_rejected(self):
        result = await update_scheduled_task(
            "sched_abc123", {"created_at": "2020-01-01T00:00:00Z"}
        )
        assert result.success is False
        assert "created_at" in result.message

    @pytest.mark.asyncio
    async def test_update_protected_field_last_run_rejected(self):
        result = await update_scheduled_task(
            "sched_abc123", {"last_run": "2020-01-01T00:00:00Z"}
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_update_without_state_manager_succeeds(self):
        result = await update_scheduled_task(
            "sched_abc123", {"message": "updated text"}
        )
        assert result.success is True
        assert result.schedule_id == "sched_abc123"

    @pytest.mark.asyncio
    async def test_update_calls_state_manager_update(self):
        sm = _mock_sm(update_return=True)
        updates = {"message": "new text", "cron_expression": "0 10 * * *"}
        result = await update_scheduled_task("sched_abc123", updates, state_manager=sm)
        sm.update_schedule_entry.assert_awaited_once_with("sched_abc123", updates)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_update_not_found_returns_error(self):
        sm = _mock_sm(update_return=False)
        result = await update_scheduled_task(
            "sched_missing", {"message": "x"}, state_manager=sm
        )
        assert result.success is False
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_update_allows_scheduled_at(self):
        """scheduled_at is a valid updatable field."""
        sm = _mock_sm(update_return=True)
        updates = {"scheduled_at": "2026-06-01T09:00:00+00:00"}
        result = await update_scheduled_task("sched_abc123", updates, state_manager=sm)
        assert result.success is True


# ===========================================================================
# pause_scheduled_task
# ===========================================================================


class TestPauseScheduledTask:

    @pytest.mark.asyncio
    async def test_pause_blank_id_returns_error(self):
        result = await pause_scheduled_task("")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_pause_without_state_manager_succeeds(self):
        result = await pause_scheduled_task("sched_abc123")
        assert result.success is True
        assert result.schedule_id == "sched_abc123"
        assert "paused" in result.message.lower()

    @pytest.mark.asyncio
    async def test_pause_calls_update_with_paused_true(self):
        """pause_scheduled_task must set paused=True via update_schedule_entry."""
        sm = _mock_sm(update_return=True)
        result = await pause_scheduled_task("sched_abc123", state_manager=sm)
        sm.update_schedule_entry.assert_awaited_once_with("sched_abc123", {"paused": True})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_pause_not_found_returns_error(self):
        sm = _mock_sm(update_return=False)
        result = await pause_scheduled_task("sched_missing", state_manager=sm)
        assert result.success is False
        assert "not found" in result.message.lower()


# ===========================================================================
# resume_scheduled_task
# ===========================================================================


class TestResumeScheduledTask:

    @pytest.mark.asyncio
    async def test_resume_blank_id_returns_error(self):
        result = await resume_scheduled_task("  ")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_resume_without_state_manager_succeeds(self):
        result = await resume_scheduled_task("sched_abc123")
        assert result.success is True
        assert "resumed" in result.message.lower()

    @pytest.mark.asyncio
    async def test_resume_calls_update_with_paused_false(self):
        """resume_scheduled_task must set paused=False via update_schedule_entry."""
        sm = _mock_sm(update_return=True)
        result = await resume_scheduled_task("sched_abc123", state_manager=sm)
        sm.update_schedule_entry.assert_awaited_once_with("sched_abc123", {"paused": False})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_resume_not_found_returns_error(self):
        sm = _mock_sm(update_return=False)
        result = await resume_scheduled_task("sched_missing", state_manager=sm)
        assert result.success is False


# ===========================================================================
# Tool spec shape tests
# ===========================================================================


class TestToolSpecShapes:

    def _assert_valid_spec(self, spec: dict, expected_name: str, required_params: list[str]):
        assert spec["name"] == expected_name
        assert isinstance(spec["description"], str) and len(spec["description"]) > 10
        params = spec["parameters"]
        assert params["type"] == "object"
        for p in required_params:
            assert p in params["properties"], f"Missing property '{p}' in {expected_name}"
        assert params.get("required") == required_params

    def test_delete_schedule_tool_spec(self):
        self._assert_valid_spec(DELETE_SCHEDULE_TOOL_SPEC, "delete_schedule", ["schedule_id"])

    def test_update_schedule_tool_spec(self):
        self._assert_valid_spec(UPDATE_SCHEDULE_TOOL_SPEC, "update_schedule", ["schedule_id"])
        # Optional update-able fields must be documented in properties
        props = UPDATE_SCHEDULE_TOOL_SPEC["parameters"]["properties"]
        assert "message" in props
        assert "cron_expression" in props
        assert "scheduled_at" in props

    def test_pause_schedule_tool_spec(self):
        self._assert_valid_spec(PAUSE_SCHEDULE_TOOL_SPEC, "pause_schedule", ["schedule_id"])

    def test_resume_schedule_tool_spec(self):
        self._assert_valid_spec(RESUME_SCHEDULE_TOOL_SPEC, "resume_schedule", ["schedule_id"])

    def test_all_specs_importable_via_package(self):
        """Verify that all four specs are re-exported from app.progenitor.tools."""
        from app.progenitor.tools import (
            DELETE_SCHEDULE_TOOL_SPEC as D,
            PAUSE_SCHEDULE_TOOL_SPEC as P,
            RESUME_SCHEDULE_TOOL_SPEC as R,
            UPDATE_SCHEDULE_TOOL_SPEC as U,
        )
        assert D["name"] == "delete_schedule"
        assert U["name"] == "update_schedule"
        assert P["name"] == "pause_schedule"
        assert R["name"] == "resume_schedule"

    def test_all_handlers_importable_via_package(self):
        """Verify that all four handler functions are re-exported from app.progenitor.tools."""
        from app.progenitor.tools import (
            delete_scheduled_task,
            pause_scheduled_task,
            resume_scheduled_task,
            update_scheduled_task,
        )
        import asyncio
        assert asyncio.iscoroutinefunction(delete_scheduled_task)
        assert asyncio.iscoroutinefunction(update_scheduled_task)
        assert asyncio.iscoroutinefunction(pause_scheduled_task)
        assert asyncio.iscoroutinefunction(resume_scheduled_task)
