"""Tests for app.progenitor.plugins.sources."""

from __future__ import annotations

import io
import json
import tarfile
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.progenitor.plugins.sources import (
    _MAX_FILE_BYTES,
    MarketplaceCatalog,
    PluginFetchError,
    _build_codeload_url,
    _extract_archive,
    _extract_skills_from_files,
    _fetch_url,
    _parse_github_source,
    _resolve_plugin_source,
    compute_digest,
    fetch_marketplace,
    fetch_plugin_files_with_report,
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
    with pytest.raises(PluginFetchError, match="must be a GitHub repo reference"):
        _parse_github_source("not-a-repo")


# ---------------------------------------------------------------------------
# _build_codeload_url
# ---------------------------------------------------------------------------


def test_build_codeload_url() -> None:
    assert _build_codeload_url("acme", "formatter", "v1") == (
        "https://codeload.github.com/acme/formatter/tar.gz/v1"
    )


# ---------------------------------------------------------------------------
# _fetch_url (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_url_rejects_non_http_scheme() -> None:
    with pytest.raises(PluginFetchError, match="Only http:// and https://"):
        await _fetch_url("ftp://example.com/file")


@pytest.mark.asyncio
async def test_fetch_url_rejects_private_host() -> None:
    with pytest.raises(PluginFetchError, match="public hostname"):
        await _fetch_url("http://localhost/file")


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
        pytest.raises(PluginFetchError, match="Redirect to private"),
    ):
        await _fetch_url("http://example.com/file")


# ---------------------------------------------------------------------------
# _extract_archive
# ---------------------------------------------------------------------------


def _make_tar_bytes(entries: dict[str, bytes]) -> bytes:
    """Build a synthetic tar.gz in memory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_archive_strips_github_prefix() -> None:
    raw = _make_tar_bytes({"owner-repo-ab12/.claude-plugin/marketplace.json": b'{"name":"x"}'})
    files = _extract_archive(raw)
    assert ".claude-plugin/marketplace.json" in files
    assert files[".claude-plugin/marketplace.json"] == b'{"name":"x"}'


def test_extract_archive_rejects_path_traversal() -> None:
    raw = _make_tar_bytes({"owner-repo-ab12/../../etc/passwd": b"root"})
    with pytest.raises(PluginFetchError, match="escapes root"):
        _extract_archive(raw)


def test_extract_archive_rejects_absolute_path() -> None:
    raw = _make_tar_bytes({"/etc/passwd": b"root"})
    with pytest.raises(PluginFetchError, match="absolute path"):
        _extract_archive(raw)


def test_extract_archive_rejects_size_cap() -> None:
    # 33 MiB + 1 byte > 32 MiB cap
    big = b"x" * (33_554_432 + 1)
    raw = _make_tar_bytes({"owner-repo-ab12/bigfile": big})
    with pytest.raises(PluginFetchError, match="exceeds 32 MiB"):
        _extract_archive(raw)


def test_extract_archive_skips_directories() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="owner-repo-ab12/skills/")
        info.type = tarfile.DIRTYPE
        tf.addfile(info)
    raw = buf.getvalue()
    files = _extract_archive(raw)
    assert "skills/" not in files


def test_extracts_object_agent_system_files_alongside_skills():
    from app.progenitor.plugins.sources import extract_plugin_content_files

    content = extract_plugin_content_files(
        {
            "skills/review/SKILL.md": b"# Review",
            "agents/objects/reviewer/SYSTEM.md": (
                b"---\nname: reviewer\ndescription: Reviews\nversion: 1.0.0\nkind: object\n---\nReview."
            ),
        },
        "demo",
    )

    assert content.skills == {"review/SKILL.md": "# Review"}
    assert "reviewer/SYSTEM.md" in content.agents


# ---------------------------------------------------------------------------
# fetch_marketplace (mocked archive download)
# ---------------------------------------------------------------------------


def _make_marketplace_archive(data: dict[str, Any]) -> bytes:
    return _make_tar_bytes(
        {"owner-repo-ab12/.claude-plugin/marketplace.json": json.dumps(data).encode()}
    )


@pytest.mark.asyncio
async def test_fetch_marketplace_parses_required_fields() -> None:
    archive = _make_marketplace_archive(
        {
            "name": "Acme Tools",
            "owner": {"name": "Acme Corp"},
            "plugins": [{"name": "formatter", "source": {"source": "github", "repo": "acme/fmt"}}],
        }
    )
    with patch("app.progenitor.plugins.sources._fetch_url", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = archive
        catalog = await fetch_marketplace("acme/tools")
    assert catalog.name == "Acme Tools"
    assert catalog.owner == {"name": "Acme Corp"}
    assert len(catalog.plugins) == 1
    assert catalog.plugins[0]["name"] == "formatter"


@pytest.mark.asyncio
async def test_fetch_marketplace_404_raises() -> None:
    from aiohttp import ClientResponseError

    mock_exc = ClientResponseError(
        request_info=AsyncMock(),
        history=(),
        status=404,
    )
    with (
        patch(
            "app.progenitor.plugins.sources._fetch_url",
            side_effect=mock_exc,
        ),
        pytest.raises(PluginFetchError, match=r"not found \(404\)"),
    ):
        await fetch_marketplace("acme/nothing")


@pytest.mark.asyncio
async def test_fetch_marketplace_missing_json_raises() -> None:
    archive = _make_tar_bytes({"owner-repo-ab12/README.md": b"hi"})
    with patch("app.progenitor.plugins.sources._fetch_url", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = archive
        with pytest.raises(PluginFetchError, match="marketplace.json not found"):
            await fetch_marketplace("acme/tools")


@pytest.mark.asyncio
async def test_fetch_marketplace_bad_json_raises() -> None:
    archive = _make_tar_bytes({"owner-repo-ab12/.claude-plugin/marketplace.json": b"not json"})
    with patch("app.progenitor.plugins.sources._fetch_url", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = archive
        with pytest.raises(PluginFetchError, match="not valid JSON"):
            await fetch_marketplace("acme/tools")


@pytest.mark.asyncio
async def test_fetch_marketplace_missing_name_raises() -> None:
    archive = _make_marketplace_archive({"owner": {"name": "x"}, "plugins": []})
    with patch("app.progenitor.plugins.sources._fetch_url", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = archive
        with pytest.raises(PluginFetchError, match="missing required field 'name'"):
            await fetch_marketplace("acme/tools")


@pytest.mark.asyncio
async def test_fetch_marketplace_keeps_archive_files() -> None:
    archive = _make_tar_bytes(
        {
            "owner-repo-ab12/.claude-plugin/marketplace.json": json.dumps(
                {
                    "name": "Acme",
                    "owner": {"name": "x"},
                    "plugins": [],
                    "metadata": {"pluginRoot": "plugins"},
                }
            ).encode(),
            "owner-repo-ab12/plugins/foo/SKILL.md": b"# Foo",
        }
    )
    with patch("app.progenitor.plugins.sources._fetch_url", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = archive
        catalog = await fetch_marketplace("acme/tools")
    assert catalog._archive_files["plugins/foo/SKILL.md"] == b"# Foo"


# ---------------------------------------------------------------------------
# _resolve_plugin_source
# ---------------------------------------------------------------------------


def test_resolve_plugin_source_github() -> None:
    entry = {"name": "fmt", "source": {"source": "github", "repo": "acme/fmt", "ref": "v1"}}
    assert _resolve_plugin_source(entry, MarketplaceCatalog("", {}, [])) == (
        "acme",
        "fmt",
        "v1",
    )


def test_resolve_plugin_source_sha_wins_over_ref() -> None:
    entry = {
        "name": "fmt",
        "source": {"source": "github", "repo": "acme/fmt", "ref": "v1", "sha": "abcd"},
    }
    assert _resolve_plugin_source(entry, MarketplaceCatalog("", {}, [])) == (
        "acme",
        "fmt",
        "abcd",
    )


def test_resolve_plugin_source_git_subdir() -> None:
    entry = {
        "name": "fmt",
        "source": {
            "source": "git-subdir",
            "url": "acme/monorepo",
            "path": "plugins/fmt",
            "ref": "main",
        },
    }
    assert _resolve_plugin_source(entry, MarketplaceCatalog("", {}, [])) == (
        "acme",
        "monorepo",
        "main",
    )


def test_resolve_plugin_source_rejects_npm() -> None:
    entry = {"name": "bad", "source": {"source": "npm", "package": "x"}}
    with pytest.raises(PluginFetchError, match="unsupported source type 'npm'"):
        _resolve_plugin_source(entry, MarketplaceCatalog("", {}, []))


def test_resolve_plugin_source_rejects_non_github_url() -> None:
    entry = {"name": "bad", "source": {"source": "url", "url": "https://evil.com/x"}}
    with pytest.raises(PluginFetchError, match="non-GitHub url source"):
        _resolve_plugin_source(entry, MarketplaceCatalog("", {}, []))


def test_resolve_plugin_source_accepts_github_url() -> None:
    entry = {
        "name": "ok",
        "source": {"source": "url", "url": "https://github.com/acme/fmt"},
    }
    assert _resolve_plugin_source(entry, MarketplaceCatalog("", {}, [])) == (
        "acme",
        "fmt",
        "HEAD",
    )


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


def test_extract_skills_raises_when_no_skills_found() -> None:
    files = {"README.md": b"# Readme"}
    with pytest.raises(PluginFetchError, match="plugin ships no skills"):
        _extract_skills_from_files(files, "my-plugin")


def test_extract_skills_excludes_out_of_scope_paths() -> None:
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


# ---------------------------------------------------------------------------
# fetch_plugin_files_with_report (mocked archive)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_plugin_files_relative_path() -> None:
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "x"},
        plugins=[{"name": "formatter", "source": "./formatter"}],
        metadata={"pluginRoot": "plugins"},
        _archive_files={
            "plugins/formatter/SKILL.md": b"# Formatter",
            "plugins/formatter/helper.md": b"help",
        },
    )
    with patch("app.progenitor.plugins.sources._fetch_url") as mock_fetch:
        # relative paths must NOT trigger a network fetch
        files, skipped = await fetch_plugin_files_with_report(catalog, "formatter")
        mock_fetch.assert_not_called()
    assert "formatter/SKILL.md" in files
    assert "formatter/helper.md" in files
    assert skipped == 0


@pytest.mark.asyncio
async def test_fetch_plugin_files_relative_path_at_archive_root() -> None:
    """A plugin whose source is exactly "./" (no pluginRoot) covers the whole
    marketplace archive -- the empty-relative-path edge case. Found via a live
    fetch against the real anthropics/skills marketplace, whose plugin entries
    use `"source": "./"` for a whole-repo plugin."""
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "x"},
        plugins=[{"name": "everything", "source": "./"}],
        _archive_files={
            "skills/xlsx/SKILL.md": b"# Xlsx",
            "skills/docx/SKILL.md": b"# Docx",
        },
    )
    files, skipped = await fetch_plugin_files_with_report(catalog, "everything")
    assert "xlsx/SKILL.md" in files
    assert "docx/SKILL.md" in files
    assert skipped == 0


@pytest.mark.asyncio
async def test_fetch_plugin_files_github_source() -> None:
    plugin_archive = _make_tar_bytes(
        {
            "owner-repo-ab12/skills/review/SKILL.md": b"# Review",
            "owner-repo-ab12/skills/review/ref.md": b"refs",
        }
    )
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "x"},
        plugins=[
            {"name": "review", "source": {"source": "github", "repo": "acme/review", "ref": "v1"}}
        ],
    )
    with patch("app.progenitor.plugins.sources._fetch_url", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = plugin_archive
        files, skipped = await fetch_plugin_files_with_report(catalog, "review")
    assert "review/SKILL.md" in files
    assert "review/ref.md" in files
    assert skipped == 0


@pytest.mark.asyncio
async def test_fetch_plugin_files_git_subdir_source() -> None:
    plugin_archive = _make_tar_bytes(
        {
            "owner-repo-ab12/plugins/review/skills/review/SKILL.md": b"# Review",
        }
    )
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "x"},
        plugins=[
            {
                "name": "review",
                "source": {
                    "source": "git-subdir",
                    "url": "acme/monorepo",
                    "path": "plugins/review",
                    "ref": "main",
                },
            }
        ],
    )
    with patch("app.progenitor.plugins.sources._fetch_url", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = plugin_archive
        files, skipped = await fetch_plugin_files_with_report(catalog, "review")
    assert "review/SKILL.md" in files
    assert skipped == 0


@pytest.mark.asyncio
async def test_fetch_plugin_files_not_found() -> None:
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "x"},
        plugins=[{"name": "missing", "source": {"source": "github", "repo": "acme/missing"}}],
    )
    from aiohttp import ClientResponseError

    mock_exc = ClientResponseError(
        request_info=AsyncMock(),
        history=(),
        status=404,
    )
    with (
        patch(
            "app.progenitor.plugins.sources._fetch_url",
            side_effect=mock_exc,
        ),
        pytest.raises(PluginFetchError, match=r"not found \(404\)"),
    ):
        await fetch_plugin_files_with_report(catalog, "missing")


@pytest.mark.asyncio
async def test_fetch_plugin_files_no_skills() -> None:
    plugin_archive = _make_tar_bytes({"owner-repo-ab12/README.md": b"# Just a readme"})
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "x"},
        plugins=[{"name": "readme-only", "source": {"source": "github", "repo": "acme/empty"}}],
    )
    with patch("app.progenitor.plugins.sources._fetch_url", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = plugin_archive
        with pytest.raises(PluginFetchError, match="plugin ships no skills"):
            await fetch_plugin_files_with_report(catalog, "readme-only")


@pytest.mark.asyncio
async def test_fetch_plugin_files_rejects_npm() -> None:
    catalog = MarketplaceCatalog(
        name="Acme",
        owner={"name": "x"},
        plugins=[{"name": "bad", "source": {"source": "npm", "package": "x"}}],
    )
    with pytest.raises(PluginFetchError, match="unsupported source type 'npm'"):
        await fetch_plugin_files_with_report(catalog, "bad")
