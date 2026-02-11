"""Tests for the self-reflection tool."""


import pytest

from app.progenitor.tools.self_reflect import (
    SELF_REFLECT_TOOL_SPEC,
    SelfReflection,
    execute_self_reflect,
)


class TestSelfReflection:
    """Test the SelfReflection class."""

    def test_list_modules(self):
        """Test listing top-level modules."""
        reflection = SelfReflection()
        modules = reflection.list_modules()

        assert len(modules) > 0
        # Should find some key directories
        module_names = [m.name for m in modules]
        assert "nodes" in module_names or any("nodes" in str(m.path) for m in modules)

    def test_list_directory_root(self):
        """Test listing the root directory."""
        reflection = SelfReflection()
        listing = reflection.list_directory("")

        assert listing.directory == "."
        assert listing.total_files > 0 or listing.total_dirs > 0

    def test_list_directory_nodes(self):
        """Test listing the nodes directory."""
        reflection = SelfReflection()
        listing = reflection.list_directory("nodes")

        assert listing.directory == "nodes"
        assert listing.total_files > 0
        # Should find decide.py
        file_names = [e.name for e in listing.entries]
        assert "decide.py" in file_names

    def test_read_file(self):
        """Test reading a file."""
        reflection = SelfReflection()
        content = reflection.read_file("config.py")

        assert content.path == "config.py"
        assert len(content.content) > 0
        assert content.language == "python"

    def test_read_file_with_max_lines(self):
        """Test reading a file with line limit."""
        reflection = SelfReflection()
        content = reflection.read_file("config.py", max_lines=10)

        lines = content.content.split('\n')
        # Should be truncated
        assert len(lines) <= 15  # 10 lines + truncation message

    def test_read_file_not_found(self):
        """Test reading a non-existent file."""
        reflection = SelfReflection()
        with pytest.raises(FileNotFoundError):
            reflection.read_file("nonexistent.py")

    def test_read_file_unsafe_path(self):
        """Test that unsafe paths are rejected."""
        reflection = SelfReflection()
        with pytest.raises(ValueError):
            reflection.read_file("../../etc/passwd")

    def test_search_code(self):
        """Test searching code."""
        reflection = SelfReflection()
        results = reflection.search_code("async def", directory="nodes")

        assert results.query == "async def"
        assert results.files_searched > 0
        assert results.total_matches > 0

    def test_search_code_regex(self):
        """Test searching code with regex."""
        reflection = SelfReflection()
        results = reflection.search_code(r"def \w+_node", directory="nodes", is_regex=True)

        assert results.files_searched > 0

    def test_find_relevant_files(self):
        """Test finding relevant files by topic."""
        reflection = SelfReflection()
        files = reflection.find_relevant_files("decision")

        assert len(files) > 0
        # Should find decide.py
        file_paths = [f.path for f in files]
        assert any("decide" in p for p in file_paths)

    def test_get_module_overview(self):
        """Test getting module overview."""
        reflection = SelfReflection()
        overview = reflection.get_module_overview("tools")

        assert overview["module"] == "tools"
        assert len(overview["files"]) > 0
        # Should find self_reflect.py
        assert "self_reflect.py" in overview["files"]


class TestExecuteSelfReflect:
    """Test the execute_self_reflect function."""

    @pytest.mark.asyncio
    async def test_list_modules_operation(self):
        """Test list_modules operation."""
        result = await execute_self_reflect(operation="list_modules")

        assert "modules" in result
        assert result["total"] > 0

    @pytest.mark.asyncio
    async def test_explore_operation(self):
        """Test explore operation."""
        result = await execute_self_reflect(operation="explore", directory="nodes")

        assert result["directory"] == "nodes"
        assert result["total_files"] > 0

    @pytest.mark.asyncio
    async def test_read_operation(self):
        """Test read operation."""
        result = await execute_self_reflect(operation="read", file_path="config.py")

        assert result["path"] == "config.py"
        assert len(result["content"]) > 0

    @pytest.mark.asyncio
    async def test_read_operation_missing_path(self):
        """Test read operation without file_path raises error."""
        with pytest.raises(ValueError, match="file_path required"):
            await execute_self_reflect(operation="read")

    @pytest.mark.asyncio
    async def test_search_operation(self):
        """Test search operation."""
        result = await execute_self_reflect(
            operation="search",
            query="async def",
            directory="nodes",
        )

        assert result["query"] == "async def"
        assert result["files_searched"] > 0

    @pytest.mark.asyncio
    async def test_search_operation_missing_query(self):
        """Test search operation without query raises error."""
        with pytest.raises(ValueError, match="query required"):
            await execute_self_reflect(operation="search")

    @pytest.mark.asyncio
    async def test_find_relevant_operation(self):
        """Test find_relevant operation."""
        result = await execute_self_reflect(
            operation="find_relevant",
            topic="scheduling",
        )

        assert result["topic"] == "scheduling"
        assert len(result["relevant_files"]) > 0

    @pytest.mark.asyncio
    async def test_module_overview_operation(self):
        """Test module_overview operation."""
        result = await execute_self_reflect(
            operation="module_overview",
            module_name="state",
        )

        assert result["module"] == "state"
        assert len(result["files"]) > 0

    @pytest.mark.asyncio
    async def test_unknown_operation(self):
        """Test unknown operation raises error."""
        with pytest.raises(ValueError, match="Unknown operation"):
            await execute_self_reflect(operation="unknown_op")


class TestToolSpec:
    """Test the tool specification."""

    def test_tool_spec_structure(self):
        """Test that tool spec has required fields."""
        assert SELF_REFLECT_TOOL_SPEC["name"] == "self_reflect"
        assert "description" in SELF_REFLECT_TOOL_SPEC
        assert "parameters" in SELF_REFLECT_TOOL_SPEC

    def test_tool_spec_parameters(self):
        """Test tool spec parameters."""
        params = SELF_REFLECT_TOOL_SPEC["parameters"]
        assert params["type"] == "object"
        assert "operation" in params["properties"]
        assert "required" in params
        assert "operation" in params["required"]

    def test_tool_spec_operations(self):
        """Test that all operations are documented."""
        operations = SELF_REFLECT_TOOL_SPEC["parameters"]["properties"]["operation"]["enum"]
        expected_ops = ["list_modules", "explore", "read", "search", "find_relevant", "module_overview"]
        for op in expected_ops:
            assert op in operations
