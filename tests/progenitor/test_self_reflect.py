"""Tests for the self-reflection tool."""

import pytest

from src.progenitor.tools.self_reflect import SelfReflection, execute_self_reflect


class TestSelfReflectionSafety:
    """Self-reflection must not read unavailable or escaping paths."""

    def test_read_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            SelfReflection().read_file("nonexistent.py")

    def test_read_file_unsafe_path(self):
        with pytest.raises(ValueError):
            SelfReflection().read_file("../../etc/passwd")


class TestExecuteSelfReflect:
    """Test the SelfReflection dispatcher rejects incomplete requests."""

    @pytest.mark.asyncio
    async def test_read_operation_missing_path(self):
        with pytest.raises(ValueError, match="file_path required"):
            await execute_self_reflect(operation="read")

    @pytest.mark.asyncio
    async def test_search_operation_missing_query(self):
        with pytest.raises(ValueError, match="query required"):
            await execute_self_reflect(operation="search")

    @pytest.mark.asyncio
    async def test_unknown_operation(self):
        with pytest.raises(ValueError, match="Unknown operation"):
            await execute_self_reflect(operation="unknown_op")
