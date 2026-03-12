from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.progenitor.tools.gh import gh_pr_create


@pytest.mark.asyncio
async def test_gh_pr_create_extracts_url_from_output() -> None:
    with patch(
        "app.progenitor.tools.gh._run_gh",
        new=AsyncMock(
            return_value={
                "returncode": 0,
                "stdout": "Created pull request\nhttps://github.com/o/r/pull/42\n",
                "stderr": "",
            }
        ),
    ):
        success, result = await gh_pr_create(
            title="Test PR",
            body="Body",
            head="branch",
            cwd=Path("."),
        )

    assert success is True
    assert result == "https://github.com/o/r/pull/42"


@pytest.mark.asyncio
async def test_gh_pr_create_fails_without_pr_url() -> None:
    with patch(
        "app.progenitor.tools.gh._run_gh",
        new=AsyncMock(
            return_value={
                "returncode": 0,
                "stdout": "created successfully",
                "stderr": "",
            }
        ),
    ):
        success, result = await gh_pr_create(
            title="Test PR",
            body="Body",
            head="branch",
            cwd=Path("."),
        )

    assert success is False
    assert "no PR URL found" in result
