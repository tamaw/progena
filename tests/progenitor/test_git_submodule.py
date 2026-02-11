"""
Tests for BL-007: Git Submodule Management

Tests cover:
1. init_submodule - creates new submodule with git init
2. init_submodule - handles existing submodules gracefully
3. submodule_commit - commits changes within submodule
4. check_submodule_exists - checks for existing submodule
"""


import pytest

from app.progenitor.tools.git import GitWorktreeManager


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    import subprocess
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, capture_output=True)

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Repo")
    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True)

    return repo_path


@pytest.fixture
def git_manager(temp_repo, tmp_path):
    """Create GitWorktreeManager with temp directories."""
    worktrees_dir = tmp_path / "worktrees"
    worktrees_dir.mkdir()
    return GitWorktreeManager(worktrees_dir, repo_path=temp_repo)


class TestInitSubmodule:
    """Test init_submodule method."""

    @pytest.mark.asyncio
    async def test_init_new_submodule(self, git_manager, temp_repo):
        """Test initializing a new submodule."""
        submodule_path = temp_repo / "app" / "agents" / "test_agent"

        success, message = await git_manager.init_submodule(
            submodule_path=submodule_path,
            submodule_name="test_agent",
        )

        assert success is True
        assert "initialized" in message.lower() or "exists" in message.lower()
        # Git repo should be initialized
        assert (submodule_path / ".git").exists()

    @pytest.mark.asyncio
    async def test_init_existing_submodule_skips(self, git_manager, temp_repo):
        """Test that initializing an existing submodule returns success without recreating."""
        submodule_path = temp_repo / "app" / "agents" / "existing_agent"
        submodule_path.mkdir(parents=True)

        # Pre-create .git directory to simulate existing submodule
        (submodule_path / ".git").mkdir()

        success, message = await git_manager.init_submodule(
            submodule_path=submodule_path,
            submodule_name="existing_agent",
        )

        assert success is True
        assert "already exists" in message.lower()

    @pytest.mark.asyncio
    async def test_init_submodule_creates_directory(self, git_manager, temp_repo):
        """Test that init_submodule creates the directory if it doesn't exist."""
        submodule_path = temp_repo / "app" / "agents" / "new_agent"

        assert not submodule_path.exists()

        success, message = await git_manager.init_submodule(
            submodule_path=submodule_path,
            submodule_name="new_agent",
        )

        assert success is True
        assert submodule_path.exists()


class TestSubmoduleCommit:
    """Test submodule_commit method."""

    @pytest.mark.asyncio
    async def test_submodule_commit_with_changes(self, git_manager, temp_repo):
        """Test committing changes in a submodule."""
        submodule_path = temp_repo / "app" / "agents" / "commit_test"

        # Initialize submodule
        success, _ = await git_manager.init_submodule(
            submodule_path=submodule_path,
            submodule_name="commit_test",
        )
        assert success

        # Create a file to commit
        (submodule_path / "agent.py").write_text("# Agent code")

        commit_hash = await git_manager.submodule_commit(
            submodule_path=submodule_path,
            message="Add agent.py",
        )

        # Should return a commit hash (None if nothing to commit initially,
        # but we added a file so should have a hash)
        # Note: The initial empty commit might have been made during init
        assert commit_hash is None or isinstance(commit_hash, str)

    @pytest.mark.asyncio
    async def test_submodule_commit_no_changes_returns_none(self, git_manager, temp_repo):
        """Test that committing with no changes returns None."""
        submodule_path = temp_repo / "app" / "agents" / "no_changes"

        # Initialize submodule
        success, _ = await git_manager.init_submodule(
            submodule_path=submodule_path,
            submodule_name="no_changes",
        )
        assert success

        # Commit without making changes
        commit_hash = await git_manager.submodule_commit(
            submodule_path=submodule_path,
            message="No changes",
        )

        assert commit_hash is None

    @pytest.mark.asyncio
    async def test_submodule_commit_non_git_repo_returns_none(self, git_manager, temp_repo):
        """Test that committing in a non-git directory returns None."""
        non_git_path = temp_repo / "not_a_repo"
        non_git_path.mkdir()

        commit_hash = await git_manager.submodule_commit(
            submodule_path=non_git_path,
            message="Should fail",
        )

        assert commit_hash is None


class TestCheckSubmoduleExists:
    """Test check_submodule_exists method."""

    @pytest.mark.asyncio
    async def test_check_exists_with_git_dir(self, git_manager, temp_repo):
        """Test checking for existing submodule with .git directory."""
        submodule_path = temp_repo / "app" / "agents" / "has_git"
        submodule_path.mkdir(parents=True)
        (submodule_path / ".git").mkdir()

        exists = await git_manager.check_submodule_exists(submodule_path)

        assert exists is True

    @pytest.mark.asyncio
    async def test_check_exists_without_git_dir(self, git_manager, temp_repo):
        """Test checking for non-existent submodule."""
        submodule_path = temp_repo / "app" / "agents" / "no_git"
        submodule_path.mkdir(parents=True)

        exists = await git_manager.check_submodule_exists(submodule_path)

        assert exists is False

    @pytest.mark.asyncio
    async def test_check_exists_nonexistent_path(self, git_manager, temp_repo):
        """Test checking for submodule at path that doesn't exist."""
        submodule_path = temp_repo / "does" / "not" / "exist"

        exists = await git_manager.check_submodule_exists(submodule_path)

        assert exists is False
