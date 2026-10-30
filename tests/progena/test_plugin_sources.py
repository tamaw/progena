"""Tests for src.progena.plugins.sources."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.progena.plugins.sources import (
    _MAX_FILE_BYTES,
    MarketplaceCatalog,
    PluginFetchError,
    PluginFetchErrorCode,
    PluginFetchErrorKind,
    _extract_skills_from_files,
    _fetch_url,
    _parse_github_source,
    _resolve_commit_sha,
    compute_digest,
    fetch_marketplace,
    fetch_plugin_content_files_with_report,
    fetch_plugin_files_with_report,
    fetch_plugin_skill_content,
    resolve_plugin_version,
)

# ---------------------------------------------------------------------------
# compute_digest
# ---------------------------------------------------------------------------


def test_compute_digest_is_deterministic() -> None:
    files = {"a.md": "alpha", "b.md": "beta"}
    d1 = compute_digest(files)
    d2 = compute_digest(files)
    assert d1 == d2
    assert d1.startswith("sha256:")


def test_compute_digest_is_order_independent() -> None:
    d1 = compute_digest({"a.md": "alpha", "b.md": "beta"})
    d2 = compute_digest({"b.md": "beta", "a.md": "alpha"})
    assert d1 == d2


def test_compute_digest_changes_when_content_changes() -> None:
    d1 = compute_digest({"a.md": "alpha"})
    d2 = compute_digest({"a.md": "ALPHA"})
    assert d1 != d2


# ---------------------------------------------------------------------------
# resolve_plugin_version
# ---------------------------------------------------------------------------


def test_resolve_plugin_version_manifest_wins() -> None:
    entry = {"version": "1.0.0", "source": {"ref": "abc123"}}
    manifest = {"version": "2.0.0"}
    assert resolve_plugin_version(entry, manifest) == "2.0.0"


def test_resolve_plugin_version_entry_wins_when_no_manifest() -> None:
    entry = {"version": "1.0.0", "source": {"ref": "abc123"}}
    assert resolve_plugin_version(entry, None) == "1.0.0"


def test_resolve_plugin_version_ref_wins_when_no_entry_version() -> None:
    entry = {"source": {"ref": "v1.2.3"}}
    assert resolve_plugin_version(entry, None) == "v1.2.3"


def test_resolve_plugin_version_sha_wins_over_ref() -> None:
    entry = {"source": {"ref": "v1.2.3", "sha": "deadbeef"}}
    assert resolve_plugin_version(entry, None) == "deadbeef"


def test_resolve_plugin_version_unknown_fallback() -> None:
    entry = {}
    assert resolve_plugin_version(entry, None) == "unknown"


# ---------------------------------------------------------------------------
# _parse_github_source
# ---------------------------------------------------------------------------


def test_parse_github_source_shorthand() -> None:
    assert _parse_github_source("acme/formatter") == ("acme", "formatter", None)


def test_parse_github_source_shorthand_with_ref() -> None:
    assert _parse_github_source("acme/formatter@v2.1.0") == ("acme", "formatter", "v2.1.0")


def test_parse_github_source_full_url() -> None:
    assert _parse_github_source("https://github.com/acme/formatter") == (
        "acme",
        "formatter",
        None,
    )


def test_parse_github_source_tree_url() -> None:
    assert _parse_github_source("https://github.com/acme/formatter/tree/main") == (
        "acme",
        "formatter",
        "main",
    )


def test_parse_github_source_invalid() -> None:
    with pytest.raises(PluginFetchError) as error:
        _parse_github_source("not-a-repo")
    assert error.value.code is PluginFetchErrorCode.FETCH_FAILED
    assert error.value.kind is PluginFetchErrorKind.FETCH


# ---------------------------------------------------------------------------
# _fetch_url (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_url_rejects_non_http_scheme() -> None:
    with pytest.raises(PluginFetchError) as error:
        await _fetch_url("ftp://example.com/file")
    assert error.value.code is PluginFetchErrorCode.FETCH_FAILED
    assert error.value.kind is PluginFetchErrorKind.FETCH


@pytest.mark.asyncio
async def test_fetch_url_rejects_private_host() -> None:
    with pytest.raises(PluginFetchError) as error:
        await _fetch_url("http://localhost/file")
    assert error.value.code is PluginFetchErrorCode.FETCH_FAILED
    assert error.value.kind is PluginFetchErrorKind.FETCH


@pytest.mark.asyncio
async def test_fetch_url_rejects_redirect_to_private() -> None:
    """Explicit redirect handling must validate the redirect target."""
    mock_response = AsyncMock()
    mock_response.status = 302
    mock_response.headers = {"location": "http://127.0.0.1/secret"}

    class _FakeContext:
        async def __aenter__(self):
            return mock_response

        async def __aexit__(self, *args):
            return False

    mock_session = AsyncMock()
    mock_session.get = lambda *_a, **_kw: _FakeContext()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("aiohttp.ClientSession", return_value=mock_session),
        pytest.raises(PluginFetchError) as error,
    ):
        await _fetch_url("http://example.com/file")
    assert error.value.code is PluginFetchErrorCode.FETCH_FAILED
    assert error.value.kind is PluginFetchErrorKind.FETCH


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "code", "kind"),
    [
        (
            403,
            {"X-RateLimit-Remaining": "0"},
            PluginFetchErrorCode.GITHUB_RATE_LIMITED,
            PluginFetchErrorKind.RATE_LIMIT,
        ),
        (403, {}, PluginFetchErrorCode.GITHUB_HTTP_ERROR, PluginFetchErrorKind.HTTP),
        (429, {}, PluginFetchErrorCode.GITHUB_RATE_LIMITED, PluginFetchErrorKind.RATE_LIMIT),
    ],
)
async def test_fetch_url_classifies_github_http_failures(
    status: int,
    headers: dict[str, str],
    code: PluginFetchErrorCode,
    kind: PluginFetchErrorKind,
) -> None:
    """GitHub quota signals, rather than a 403 message, drive classification."""
    from aiohttp import ClientResponseError

    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.headers = headers
    mock_response.raise_for_status = Mock(
        side_effect=ClientResponseError(AsyncMock(), (), status=status, headers=headers)
    )

    class _FakeContext:
        async def __aenter__(self):
            return mock_response

        async def __aexit__(self, *args):
            return False

    mock_session = AsyncMock()
    mock_session.get = lambda *_a, **_kw: _FakeContext()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("aiohttp.ClientSession", return_value=mock_session),
        pytest.raises(PluginFetchError) as error,
    ):
        await _fetch_url("https://api.github.com/repos/acme/plugin")

    assert error.value.code == code
    assert error.value.kind == kind
    assert error.value.status == status


@pytest.mark.asyncio
async def test_resolve_commit_sha_preserves_missing_commit_status() -> None:
    missing_commit = PluginFetchError(
        "GitHub request failed (HTTP 404)",
        code=PluginFetchErrorCode.GITHUB_HTTP_ERROR,
        kind=PluginFetchErrorKind.HTTP,
        status=404,
    )

    with (
        patch(
            "src.progena.plugins.sources._fetch_json_url",
            new=AsyncMock(side_effect=missing_commit),
        ),
        pytest.raises(PluginFetchError) as error,
    ):
        await _resolve_commit_sha("acme", "plugin", "missing")

    assert error.value.code is PluginFetchErrorCode.COMMIT_NOT_FOUND
    assert error.value.kind is PluginFetchErrorKind.NOT_FOUND
    assert error.value.status == 404


# ---------------------------------------------------------------------------
# fetch_marketplace (mocked archive download)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_marketplace_reads_manifest_over_http() -> None:
    manifest = {
        "name": "Awesome Copilot",
        "metadata": {"description": "Skills", "version": "1.0.0"},
        "owner": {"name": "GitHub"},
        "plugins": [{"name": "demo", "source": "plugins/demo"}],
    }

    async def fetch(url: str) -> bytes:
        if url.endswith(".claude-plugin/marketplace.json"):
            raise PluginFetchError(
                "GitHub request failed (HTTP 404)",
                code=PluginFetchErrorCode.GITHUB_HTTP_ERROR,
                kind=PluginFetchErrorKind.HTTP,
                status=404,
            )
        return json.dumps(manifest).encode()

    with (
        patch(
            "src.progena.plugins.sources._resolve_commit_sha",
            new=AsyncMock(return_value="abc123"),
        ),
        patch("src.progena.plugins.sources._fetch_url", side_effect=fetch),
    ):
        catalog = await fetch_marketplace("github/awesome-copilot")
    assert catalog.commit_sha == "abc123"
    assert catalog.repository == "github/awesome-copilot"
    assert catalog.plugins[0]["source"] == "plugins/demo"


@pytest.mark.asyncio
async def test_fetch_plugin_skill_content_preserves_missing_skill_status() -> None:
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "Acme"},
        plugins=[{"name": "demo", "source": "plugins/demo"}],
        repository="acme/tools",
        commit_sha="abc123",
    )
    missing_skill = PluginFetchError(
        "GitHub request failed (HTTP 404)",
        code=PluginFetchErrorCode.GITHUB_HTTP_ERROR,
        kind=PluginFetchErrorKind.HTTP,
        status=404,
    )

    with (
        patch(
            "src.progena.plugins.sources._fetch_url",
            new=AsyncMock(side_effect=missing_skill),
        ),
        pytest.raises(PluginFetchError) as error,
    ):
        await fetch_plugin_skill_content(catalog, "demo", "missing")

    assert error.value.code is PluginFetchErrorCode.SKILL_NOT_FOUND
    assert error.value.kind is PluginFetchErrorKind.NOT_FOUND
    assert error.value.status == 404


@pytest.mark.asyncio
async def test_fetch_plugin_skills_over_http() -> None:
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "Acme"},
        plugins=[{"name": "demo", "source": "plugins/demo"}],
        repository="acme/tools",
        commit_sha="abc123",
    )

    async def fetch(url: str) -> bytes:
        if "/git/trees/" in url:
            return json.dumps(
                {
                    "tree": [
                        {"path": "plugins/demo/skills/review/SKILL.md", "type": "blob"},
                        {"path": "plugins/demo/README.md", "type": "blob"},
                    ]
                }
            ).encode()
        return b"---\nname: review\n---\n# Review"

    with patch("src.progena.plugins.sources._fetch_url", side_effect=fetch):
        files, skipped = await fetch_plugin_files_with_report(catalog, "demo")
    assert files == {"review/SKILL.md": "---\nname: review\n---\n# Review"}
    assert skipped == 0


@pytest.mark.asyncio
async def test_fetch_plugin_agents_over_http() -> None:
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "Acme"},
        plugins=[{"name": "demo", "source": "plugins/demo"}],
        repository="acme/tools",
        commit_sha="abc123",
    )
    system = "---\nname: reviewer\ndescription: Reviews\nversion: 1.0.0\nkind: object\n---\nReview."

    async def fetch(url: str) -> bytes:
        if "/git/trees/" in url:
            return json.dumps(
                {
                    "tree": [
                        {"path": "plugins/demo/agents/objects/reviewer/SYSTEM.md", "type": "blob"},
                        {"path": "plugins/demo/skills/review/SKILL.md", "type": "blob"},
                    ]
                }
            ).encode()
        return system.encode()

    with patch("src.progena.plugins.sources._fetch_url", side_effect=fetch):
        files = await fetch_plugin_content_files_with_report(catalog, "demo")
    assert files.skills == {"review/SKILL.md": system}
    assert files.agents == {"reviewer/SYSTEM.md": system}
    assert files.skipped_count == 0


@pytest.mark.asyncio
async def test_missing_skills_directory_is_empty() -> None:
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "Acme"},
        plugins=[{"name": "demo", "source": "plugins/demo"}],
        repository="acme/tools",
        commit_sha="abc123",
    )

    async def fetch(url: str) -> bytes:
        return json.dumps({"tree": [{"path": "plugins/demo/README.md", "type": "blob"}]}).encode()

    with patch("src.progena.plugins.sources._fetch_url", side_effect=fetch):
        files, skipped = await fetch_plugin_files_with_report(catalog, "demo")
    assert files == {}
    assert skipped == 0


# ---------------------------------------------------------------------------
# _extract_skills_from_files
# ---------------------------------------------------------------------------


def test_extract_skills_prefers_skills_dir() -> None:
    files = {
        "skills/review/SKILL.md": b"# Review",
        "skills/review/reference.md": b"refs",
        "SKILL.md": b"# Root",
    }
    result, skipped = _extract_skills_from_files(files, "my-plugin")
    assert "review/SKILL.md" in result
    assert "review/reference.md" in result
    assert "SKILL.md" not in result  # root ignored when skills/ present
    assert skipped == 0


def test_extract_skills_falls_back_to_root_skill_md() -> None:
    files = {
        "SKILL.md": b"---\nname: root-skill\n---\n# Root",
        "helper.py": b"print('hi')",
    }
    result, skipped = _extract_skills_from_files(files, "my-plugin")
    assert "root-skill/SKILL.md" in result
    assert "root-skill/helper.py" in result
    assert skipped == 0


def test_extract_skills_root_fallback_uses_plugin_name_when_no_frontmatter() -> None:
    files = {"SKILL.md": b"# No frontmatter"}
    result, skipped = _extract_skills_from_files(files, "my-plugin")
    assert "my-plugin/SKILL.md" in result
    assert skipped == 0


def test_extract_skills_excludes_unsupported_directories_and_files() -> None:
    files = {
        "skills/review/SKILL.md": b"# Review",
        "skills/review/commands/help.md": b"help",
        "skills/review/hooks/pre.md": b"hook",
        "skills/review/.mcp.json": b"{}",
        "skills/review/.lsp.json": b"{}",
        "skills/review/monitors/watch.py": b"watch",
        "skills/review/agents/agent.py": b"agent",
    }
    result, skipped = _extract_skills_from_files(files, "my-plugin")
    assert "review/SKILL.md" in result
    assert "review/commands/help.md" not in result
    assert "review/hooks/pre.md" not in result
    assert "review/.mcp.json" not in result
    assert "review/.lsp.json" not in result
    assert "review/monitors/watch.py" not in result
    assert "review/agents/agent.py" not in result


def test_extract_skills_skips_non_utf8() -> None:
    files = {
        "skills/review/SKILL.md": b"# Review",
        "skills/review/binary.so": b"\x89PNG\r\n\x1a\n",
    }
    result, skipped = _extract_skills_from_files(files, "my-plugin")
    assert "review/SKILL.md" in result
    assert "review/binary.so" not in result
    assert skipped == 1


def test_extract_skills_skips_oversized_files() -> None:
    big = b"x" * (_MAX_FILE_BYTES + 1)
    files = {
        "skills/review/SKILL.md": b"# Review",
        "skills/review/big.md": big,
    }
    result, skipped = _extract_skills_from_files(files, "my-plugin")
    assert "review/SKILL.md" in result
    assert "review/big.md" not in result
    assert skipped == 1


def test_extract_skills_respects_50_file_cap() -> None:
    files = {f"skills/review/file{i:02d}.md": b"x" for i in range(55)}
    files["skills/review/SKILL.md"] = b"# Review"  # required for skill dir detection
    result, skipped = _extract_skills_from_files(files, "my-plugin")
    # 56 files total (55 siblings + SKILL.md), cap is 50
    assert len(result) == 50
    assert skipped == 6


def test_extracts_object_agents_alongside_skills() -> None:
    from src.progena.plugins.sources import extract_plugin_content_files

    content = extract_plugin_content_files(
        {
            "skills/review/SKILL.md": b"# Review",
            "agents/objects/reviewer/SYSTEM.md": (
                b"---\nname: reviewer\ndescription: Reviews\n"
                b"version: 1.0.0\nkind: object\n---\nReview."
            ),
        },
        "demo",
    )

    assert content.skills == {"review/SKILL.md": "# Review"}
    assert content.agents["reviewer/SYSTEM.md"].startswith("---")


# ---------------------------------------------------------------------------
# fetch_plugin_files_with_report (mocked archive)
# ---------------------------------------------------------------------------
