"""Fetch and parse marketplace catalogs and plugin skill files."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import tarfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import aiohttp
import yaml

from app.progenitor.utils import env_int

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSRF hardening (mirrors app/backend/attachments.py:44-63,490-493)
# ---------------------------------------------------------------------------

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
_MAX_URL_REDIRECTS = 5
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

# Basic private-address guard so that a malicious marketplace URL cannot
# resolve to loopback / link-local / RFC1918 space.
_PRIVATE_IP_PATTERNS = (
    re.compile(r"^127\."),
    re.compile(r"^10\."),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^169\.254\."),
    re.compile(r"^0\."),
    re.compile(r"^::1$"),
    re.compile(r"^fc00:"),
    re.compile(r"^fe80:"),
)

_EXCLUDED_PREFIXES = (
    "commands/",
    "agents/",
    "hooks/",
    "monitors/",
)

_EXCLUDED_FILES = {
    ".mcp.json",
    ".lsp.json",
}

_MAX_FILE_BYTES = 1_048_576  # 1 MiB
_MAX_FILES_PER_PLUGIN = 50
_DEFAULT_MAX_ARCHIVE_MB = 64


def _configured_max_archive_bytes() -> int:
    """Return the positive archive expansion limit configured by the environment."""
    configured_mb = env_int("PROGENITOR_PLUGIN_MAX_ARCHIVE_MB", _DEFAULT_MAX_ARCHIVE_MB)
    if configured_mb <= 0:
        logger.warning(
            "PROGENITOR_PLUGIN_MAX_ARCHIVE_MB must be positive; using default %d MiB",
            _DEFAULT_MAX_ARCHIVE_MB,
        )
        configured_mb = _DEFAULT_MAX_ARCHIVE_MB
    return configured_mb * 1024 * 1024


_MAX_ARCHIVE_BYTES = _configured_max_archive_bytes()


class PluginFetchError(Exception):
    """Raised when a marketplace or plugin fetch fails in an actionable way."""


@dataclass
class MarketplaceCatalog:
    """Parsed marketplace.json and its pinned GitHub repository revision."""

    name: str
    owner: dict
    plugins: list[dict]
    description: str | None = None
    version: str | None = None
    metadata: dict = field(default_factory=dict)
    repository: str | None = None
    commit_sha: str | None = None
    _archive_files: dict[str, bytes] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# URL / GitHub helpers
# ---------------------------------------------------------------------------


def _is_private_hostname(hostname: str) -> bool:
    if not hostname:
        return True
    hostname_lower = hostname.lower()
    if hostname_lower in ("localhost", "localhost.localdomain"):
        return True
    try:
        import ipaddress

        addr = ipaddress.ip_address(hostname_lower)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        for pat in _PRIVATE_IP_PATTERNS:
            if pat.search(hostname_lower):
                return True
    return False


def _parse_github_source(source: str) -> tuple[str, str, str | None]:
    """Extract (owner, repo, ref_or_none) from a GitHub reference.

    Supported shapes:
      owner/repo
      owner/repo@ref
      https://github.com/owner/repo
      https://github.com/owner/repo/tree/ref
    """
    source = source.strip()
    if source.startswith("https://github.com/"):
        path = source[len("https://github.com/") :]
        parts = path.split("/")
        if len(parts) < 2:
            raise PluginFetchError(f"invalid GitHub source URL: {source}")
        owner = parts[0]
        repo = parts[1]
        ref = None
        if len(parts) >= 4 and parts[2] == "tree":
            ref = parts[3]
        return owner, repo, ref
    if "/" not in source:
        raise PluginFetchError(f"marketplace source must be a GitHub repo reference: {source}")
    if "@" in source:
        repo_part, ref = source.rsplit("@", 1)
    else:
        repo_part, ref = source, None
    owner, repo = repo_part.split("/", 1)
    if not owner or not repo:
        raise PluginFetchError(f"invalid GitHub source: {source}")
    return owner, repo, ref


def resolve_relative_plugin_source(
    marketplace_source: str,
    plugin_source: str,
    plugin_root: str = "",
    commit_sha: str | None = None,
) -> dict[str, Any]:
    """Resolve a marketplace-relative plugin path into a durable GitHub source."""
    if not isinstance(plugin_source, str) or not plugin_source.strip():
        raise PluginFetchError(f"not a relative plugin source: {plugin_source!r}")
    if plugin_source.startswith(("http://", "https://")):
        raise PluginFetchError(f"not a relative plugin source: {plugin_source!r}")
    owner, repo, ref = _parse_github_source(marketplace_source)
    rel = plugin_source.removeprefix("./").strip("/")
    path = f"{plugin_root.rstrip('/')}/{rel}" if plugin_root else rel
    result: dict[str, Any] = {"source": "git-subdir", "url": f"{owner}/{repo}", "path": path}
    if commit_sha:
        result["sha"] = commit_sha
    elif ref:
        result["ref"] = ref
    return result


def _build_codeload_url(owner: str, repo: str, ref: str) -> str:
    return f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}"


async def _fetch_url(url: str) -> bytes:
    """SSRF-hardened GET that follows redirects explicitly.

    Mirrors the hardening in app/backend/attachments.py:
      * scheme allow-list {http, https}
      * manual redirect loop (max 5)
      * raise_for_status
    """
    parsed = urlparse(url)
    scheme = str(parsed.scheme or "").lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise PluginFetchError("Only http:// and https:// URLs are allowed")
    hostname = str(parsed.hostname or "").lower()
    if not hostname or _is_private_hostname(hostname):
        raise PluginFetchError("URL must have a public hostname")

    current_url = url
    for _attempt in range(_MAX_URL_REDIRECTS + 1):
        async with (
            aiohttp.ClientSession() as session,
            session.get(current_url, allow_redirects=False) as response,
        ):
            if response.status in _REDIRECT_STATUS_CODES:
                location = response.headers.get("location")
                if not location:
                    raise PluginFetchError("Redirect response missing Location header")
                current_url = urljoin(current_url, location)
                # Re-validate the redirect target
                rp = urlparse(current_url)
                if str(rp.scheme or "").lower() not in _ALLOWED_URL_SCHEMES:
                    raise PluginFetchError("Redirect to non-http(s) scheme is not allowed")
                if _is_private_hostname(str(rp.hostname or "").lower()):
                    raise PluginFetchError("Redirect to private address is not allowed")
                continue
            response.raise_for_status()
            return bytes(await response.read())
    raise PluginFetchError("Too many redirects while fetching URL")


async def _fetch_json_url(url: str) -> dict[str, Any]:
    try:
        raw = await _fetch_url(url)
    except aiohttp.ClientResponseError as exc:
        if exc.status == 403:
            raise PluginFetchError(
                "GitHub API rate limit exceeded; configure GITHUB_TOKEN or retry later"
            ) from exc
        raise PluginFetchError(f"GitHub API request failed ({exc.status})") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginFetchError(f"invalid JSON response from {url}: {exc}") from exc
    if not isinstance(value, dict):
        raise PluginFetchError(f"expected JSON object from {url}")
    return value


async def _resolve_commit_sha(owner: str, repo: str, ref: str) -> str:
    url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/commits/{quote(ref, safe='')}"
    try:
        payload = await _fetch_json_url(url)
    except aiohttp.ClientResponseError as exc:
        raise PluginFetchError(f"GitHub commit not found (404): {owner}/{repo}@{ref}") from exc
    sha = payload.get("sha")
    if not isinstance(sha, str) or not sha:
        raise PluginFetchError(f"GitHub commit response missing sha for {owner}/{repo}@{ref}")
    return sha


def _raw_github_url(owner: str, repo: str, sha: str, path: str) -> str:
    encoded_path = "/".join(quote(part) for part in path.strip("/").split("/"))
    return f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/{quote(sha)}/{encoded_path}"


async def _resolve_default_branch(owner: str, repo: str) -> str:
    payload = await _fetch_json_url(f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}")
    branch = payload.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise PluginFetchError(
            f"GitHub repository response missing default_branch for {owner}/{repo}"
        )
    return branch


async def _fetch_github_tree(owner: str, repo: str, sha: str, prefix: str) -> dict[str, bytes]:
    url = (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}"
        f"/git/trees/{quote(sha, safe='')}?recursive=1"
    )
    payload = await _fetch_json_url(url)
    if payload.get("truncated"):
        raise PluginFetchError(f"GitHub repository tree is too large: {owner}/{repo}")
    tree = payload.get("tree")
    if not isinstance(tree, list):
        raise PluginFetchError(f"GitHub tree response missing tree for {owner}/{repo}")
    prefix = prefix.strip("/")
    prefix_with_slash = f"{prefix}/" if prefix else ""
    files: dict[str, bytes] = {}
    for item in tree:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path.startswith(prefix_with_slash):
            continue
        relative = path[len(prefix_with_slash) :]
        if relative.startswith("skills/") or relative == "skills":
            files[relative] = await _fetch_url(_raw_github_url(owner, repo, sha, path))
    return files


# ---------------------------------------------------------------------------
# Archive helpers
# ---------------------------------------------------------------------------


def _extract_archive(archive_bytes: bytes) -> dict[str, bytes]:
    """Expand a tar.gz in-memory with path-traversal and size guards.

    Returns a mapping of *stripped* relative paths -> file contents.
    GitHub archives have a single top-level directory like
    ``owner-repo-abcdef/``; we strip that prefix so paths are repo-relative.
    """
    total_size = 0
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = member.name
            # Path-traversal guard on the raw member name BEFORE stripping the
            # GitHub top-level prefix (which may itself contain slashes).
            raw_parts = name.replace("\\", "/").split("/")
            if any(p == ".." for p in raw_parts):
                raise PluginFetchError(f"archive member escapes root: {member.name}")
            if name.startswith("/"):
                raise PluginFetchError(f"archive member has absolute path: {member.name}")
            # GitHub archives prefix everything with ``owner-repo-ref/``.
            # Strip the first path segment so paths are repo-relative.
            if "/" in name:
                _, rel = name.split("/", 1)
            else:
                rel = name
            total_size += member.size
            if total_size > _MAX_ARCHIVE_BYTES:
                raise PluginFetchError(
                    f"archive exceeds {_MAX_ARCHIVE_BYTES // 1024 // 1024} MiB uncompressed"
                )
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            files[rel] = fobj.read()
    return files


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------
_MARKETPLACE_MANIFEST_PATHS = (
    ".claude-plugin/marketplace.json",
    ".github/plugin/marketplace.json",
)


async def fetch_marketplace(source: str) -> MarketplaceCatalog:
    """Fetch and parse a GitHub marketplace manifest over direct HTTP."""
    owner, repo, ref = _parse_github_source(source)
    resolved_ref = ref or await _resolve_default_branch(owner, repo)
    commit_sha = await _resolve_commit_sha(owner, repo, resolved_ref)
    data: dict[str, Any] | None = None
    for manifest_path in _MARKETPLACE_MANIFEST_PATHS:
        try:
            raw = await _fetch_url(_raw_github_url(owner, repo, commit_sha, manifest_path))
            data = json.loads(raw.decode("utf-8"))
            break
        except aiohttp.ClientResponseError as exc:
            if exc.status != 404:
                raise PluginFetchError(f"failed to fetch marketplace manifest: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginFetchError(f"marketplace.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        paths = ", ".join(_MARKETPLACE_MANIFEST_PATHS)
        raise PluginFetchError(f"marketplace.json not found (checked: {paths})")

    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise PluginFetchError("marketplace.json missing required field 'name'")
    owner_info = data.get("owner")
    if not isinstance(owner_info, dict) or not owner_info.get("name"):
        raise PluginFetchError("marketplace.json missing required field 'owner.name'")
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise PluginFetchError("marketplace.json missing required field 'plugins'")
    metadata = data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return MarketplaceCatalog(
        name=name,
        owner=owner_info,
        plugins=plugins,
        description=data.get("description") or metadata.get("description"),
        version=data.get("version") or metadata.get("version"),
        metadata=metadata,
        repository=f"{owner}/{repo}",
        commit_sha=commit_sha,
    )


# ---------------------------------------------------------------------------
# Plugin files
# ---------------------------------------------------------------------------


def _resolve_plugin_source(entry: dict, catalog: MarketplaceCatalog) -> tuple[str, str, str | None]:
    """Resolve a plugin entry's ``source`` to an (owner, repo, ref_or_sha) triple.

    Returns the resolved ref/sha to use for the codeload URL.
    """
    src = entry.get("source")
    if isinstance(src, str) and src.startswith("./"):
        # Relative path — resolved against the marketplace archive, not a remote repo.
        # We return a sentinel that tells the caller to use the catalog archive.
        return "__marketplace__", "__marketplace__", None
    if not isinstance(src, dict):
        raise PluginFetchError(
            f"plugin '{entry.get('name')}' source must be a dict or a relative path string"
        )

    source_type = src.get("source")
    if source_type == "npm":
        raise PluginFetchError(
            f"plugin '{entry.get('name')}' uses unsupported source type 'npm'; "
            "only GitHub-based sources are supported"
        )
    if source_type == "url":
        url = src.get("url", "")
        if not url or not (
            url.startswith("https://github.com/") or url.startswith("http://github.com/")
        ):
            raise PluginFetchError(
                f"plugin '{entry.get('name')}' uses unsupported non-GitHub url source; "
                "only GitHub-based sources are supported"
            )
        # Treat GitHub url like github source
        owner, repo, ref = _parse_github_source(url)
        sha = src.get("sha")
        resolved = sha or ref or "main"
        return owner, repo, resolved
    if source_type == "github":
        repo_str = src.get("repo", "")
        if "/" not in repo_str:
            raise PluginFetchError(
                f"plugin '{entry.get('name')}' github source missing valid 'repo' field"
            )
        owner, repo = repo_str.split("/", 1)
        explicit_ref = src.get("ref")
        sha = src.get("sha")
        resolved = sha or explicit_ref or "main"
        return owner, repo, resolved
    if source_type == "git-subdir":
        url = src.get("url", "")
        if not url:
            raise PluginFetchError(f"plugin '{entry.get('name')}' git-subdir source missing 'url'")
        owner, repo, _parsed_ref = _parse_github_source(url)
        explicit_ref = src.get("ref")
        sha = src.get("sha")
        resolved = sha or explicit_ref or "main"
        return owner, repo, resolved
    raise PluginFetchError(
        f"plugin '{entry.get('name')}' uses unknown source type '{source_type}'; "
        "only GitHub-based sources are supported"
    )


async def _fetch_plugin_archive(entry: dict, catalog: MarketplaceCatalog) -> dict[str, bytes]:
    """Fetch the selected plugin's ``skills/`` files over direct GitHub HTTP."""
    src = entry.get("source")
    if catalog.repository:
        marketplace_owner, marketplace_repo = catalog.repository.split("/", 1)
    elif isinstance(src, dict):
        repository = src.get("repo") or src.get("url")
        marketplace_owner, marketplace_repo, _ = _parse_github_source(str(repository or ""))
    else:
        raise PluginFetchError("marketplace catalog is missing its repository")
    owner, repo, plugin_root = marketplace_owner, marketplace_repo, ""

    if isinstance(src, str):
        plugin_root = src.removeprefix("./").strip("/")
    elif isinstance(src, dict):
        source_type = src.get("source")
        if source_type == "git-subdir":
            owner, repo, _ = _parse_github_source(str(src.get("url") or ""))
            plugin_root = str(src.get("path") or "").strip("/")
        elif source_type == "github":
            owner, repo = str(src.get("repo") or "").split("/", 1)
            plugin_root = str(src.get("path") or "").strip("/")
        else:
            raise PluginFetchError(
                f"plugin '{entry.get('name')}' uses unsupported source type {source_type!r}"
            )
    else:
        raise PluginFetchError(f"plugin '{entry.get('name')}' has no source")

    source_ref = str(src.get("ref") or "main") if isinstance(src, dict) else "main"
    sha = (
        catalog.commit_sha
        if (owner, repo) == (marketplace_owner, marketplace_repo) and catalog.commit_sha
        else await _resolve_commit_sha(owner, repo, source_ref)
    )
    return await _fetch_github_tree(owner, repo, sha, plugin_root)


def _extract_frontmatter_name(raw: bytes, fallback: str) -> str:
    """Parse the ``name`` key from YAML frontmatter of a SKILL.md file."""
    text = raw.decode("utf-8", errors="replace")
    if not text.startswith("---"):
        return fallback
    try:
        _, frontmatter, _ = text.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        if isinstance(meta, dict):
            name = meta.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    except (ValueError, yaml.YAMLError):
        pass
    return fallback


def _extract_skills_from_files(
    files: dict[str, bytes], plugin_name: str
) -> tuple[dict[str, str], int]:
    """Extract skill files from a plugin's raw file map.

    Returns ``(path_to_text_map, skipped_count)``.
    Precedence:
      1. ``skills/<name>/SKILL.md`` directories — every file under each.
      2. A single ``SKILL.md`` at the plugin root; skill directory name is
         taken from frontmatter ``name``, falling back to ``plugin_name``.
      3. Neither found → raises PluginFetchError("plugin ships no skills").
    """
    # Global exclusions — reject any path whose components match excluded
    # directories or filenames, regardless of nesting depth.
    _EXCLUDED_DIR_NAMES = {"commands", "agents", "hooks", "monitors"}

    def _is_excluded(path: str) -> bool:
        parts = path.split("/")
        return (
            any(p in _EXCLUDED_DIR_NAMES for p in parts)
            or path in _EXCLUDED_FILES
            or parts[-1] in _EXCLUDED_FILES
        )

    # 1. Look for skill directories
    skill_names: set[str] = set()
    for path in files:
        if _is_excluded(path):
            continue
        if path.startswith("skills/") and path.endswith("/SKILL.md"):
            parts = path.split("/")
            if len(parts) >= 3:
                skill_names.add(parts[1])

    if skill_names:
        result: dict[str, str] = {}
        skipped = 0
        file_count = 0
        for path, content in files.items():
            if _is_excluded(path):
                continue
            for skill_name in skill_names:
                prefix = f"skills/{skill_name}/"
                if path.startswith(prefix):
                    if file_count >= _MAX_FILES_PER_PLUGIN:
                        skipped += 1
                        break
                    if len(content) > _MAX_FILE_BYTES:
                        skipped += 1
                        break
                    try:
                        text = content.decode("utf-8")
                        result[f"{skill_name}/{path[len(prefix) :]}"] = text
                        file_count += 1
                    except UnicodeDecodeError:
                        skipped += 1
                    break
        return result, skipped

    # 2. Root SKILL.md fallback
    root_skill = files.get("SKILL.md")
    if root_skill is not None:
        skill_name = _extract_frontmatter_name(root_skill, fallback=plugin_name)
        result = {}
        skipped = 0
        file_count = 0
        for path, content in files.items():
            if _is_excluded(path):
                continue
            # In the root-skill case we only take files that sit directly at
            # the plugin root (no subdirectories) — this mirrors the "single
            # SKILL.md at the plugin root" semantics.
            if "/" in path:
                continue
            if file_count >= _MAX_FILES_PER_PLUGIN:
                skipped += 1
                continue
            if len(content) > _MAX_FILE_BYTES:
                skipped += 1
                continue
            try:
                text = content.decode("utf-8")
                result[f"{skill_name}/{path}"] = text
                file_count += 1
            except UnicodeDecodeError:
                skipped += 1
        return result, skipped

    # 3. Nothing found
    # A marketplace plugin without a skills/ directory is valid but provides
    # no Progenitor skills.
    return {}, 0


async def fetch_plugin_files_with_report(
    catalog: MarketplaceCatalog, plugin_name: str
) -> tuple[dict[str, str], int]:
    """Fetch the named plugin's skill files and return them together with a
    count of files that were skipped (too large, non-UTF-8, or past the 50-file
    cap).

    The returned dict keys are ``<skill-name>/<filename>`` relative paths.
    """
    entry: dict[str, Any] | None = None
    for p in catalog.plugins:
        if p.get("name") == plugin_name:
            entry = p
            break
    if entry is None:
        raise PluginFetchError(f"plugin '{plugin_name}' not found in marketplace catalog")

    raw_files = await _fetch_plugin_archive(entry, catalog)
    return _extract_skills_from_files(raw_files, plugin_name)


async def fetch_plugin_files(catalog: MarketplaceCatalog, plugin_name: str) -> dict[str, str]:
    """Convenience wrapper for :func:`fetch_plugin_files_with_report` that
    returns only the file map.
    """
    files, _skipped = await fetch_plugin_files_with_report(catalog, plugin_name)
    return files


# ---------------------------------------------------------------------------
# Version & digest
# ---------------------------------------------------------------------------


def resolve_plugin_version(plugin_entry: dict, plugin_manifest: dict | None) -> str:
    """Return the canonical version string for a plugin.

    Precedence:
      1. ``plugin_manifest["version"]`` (from the plugin's own plugin.json).
      2. ``plugin_entry["version"]`` (from the marketplace catalog entry).
      3. The archive ref/sha that was used to fetch the plugin.
    """
    if plugin_manifest is not None:
        manifest_version = plugin_manifest.get("version")
        if isinstance(manifest_version, str) and manifest_version.strip():
            return manifest_version.strip()
    entry_version = plugin_entry.get("version")
    if isinstance(entry_version, str) and entry_version.strip():
        return entry_version.strip()
    src = plugin_entry.get("source", {})
    if isinstance(src, dict):
        sha = src.get("sha")
        if isinstance(sha, str) and sha.strip():
            return sha.strip()
        ref = src.get("ref")
        if isinstance(ref, str) and ref.strip():
            return ref.strip()
    return "unknown"


def compute_digest(files: dict[str, str]) -> str:
    """Compute a deterministic SHA-256 digest over a file map.

    Sorts by path, then hashes ``path\\0content`` for each entry.
    Returns ``sha256:<hex>``.
    """
    hasher = hashlib.sha256()
    for path in sorted(files):
        hasher.update(path.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(files[path].encode("utf-8"))
    return f"sha256:{hasher.hexdigest()}"
