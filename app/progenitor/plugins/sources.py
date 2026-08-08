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
from urllib.parse import urljoin, urlparse

import aiohttp
import yaml

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
_MAX_ARCHIVE_BYTES = 33_554_432  # 32 MiB


class PluginFetchError(Exception):
    """Raised when a marketplace or plugin fetch fails in an actionable way."""


@dataclass
class MarketplaceCatalog:
    """Parsed marketplace.json plus the archive needed for relative-path resolution."""

    name: str
    owner: dict
    plugins: list[dict]
    description: str | None = None
    version: str | None = None
    metadata: dict = field(default_factory=dict)
    # Internal — the extracted archive file map so that relative-path plugin
    # sources ("./x") can be resolved without a second network round-trip.
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
    marketplace_source: str, plugin_source: str, plugin_root: str = ""
) -> dict[str, Any]:
    """Turn a plugin's marketplace-relative ``source`` (e.g. ``"./"`` or
    ``"./formatter"``) into an absolute, self-sufficient source dict.

    A relative-path plugin source is only meaningful together with the
    marketplace archive it was resolved against -- ``plugins.json`` stores
    each entry's ``source`` in isolation, with no reference back to its
    marketplace, so a raw relative path can never be re-fetched by the
    reconciler. Callers that install a plugin (the backend's install route)
    must call this at install time, before persisting, so what lands in
    ``plugins.json`` is a plain GitHub ``git-subdir`` source instead.

    Only GitHub-shaped marketplace sources are supported, matching every
    other source type in this module.
    """
    if not (isinstance(plugin_source, str) and plugin_source.startswith("./")):
        raise PluginFetchError(f"not a relative plugin source: {plugin_source!r}")
    owner, repo, ref = _parse_github_source(marketplace_source)
    rel = plugin_source[2:]
    path = f"{plugin_root.rstrip('/')}/{rel}" if plugin_root else rel
    path = path.strip("/")
    result: dict[str, Any] = {"source": "git-subdir", "url": f"{owner}/{repo}", "path": path}
    if ref:
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


async def fetch_marketplace(source: str) -> MarketplaceCatalog:
    """Download a marketplace archive, extract ``.claude-plugin/marketplace.json``,
    and return the parsed catalog together with the full archive file map so that
    relative-path plugin sources can be resolved later.
    """
    owner, repo, ref = _parse_github_source(source)
    resolved_ref = ref or "HEAD"
    archive_url = _build_codeload_url(owner, repo, resolved_ref)

    try:
        archive_bytes = await _fetch_url(archive_url)
    except aiohttp.ClientResponseError as exc:
        if exc.status == 404:
            raise PluginFetchError(f"marketplace archive not found (404): {archive_url}") from exc
        raise PluginFetchError(f"failed to download marketplace archive: {exc}") from exc
    except Exception as exc:
        raise PluginFetchError(f"failed to download marketplace archive: {exc}") from exc

    try:
        archive_files = _extract_archive(archive_bytes)
    except PluginFetchError:
        raise
    except Exception as exc:
        raise PluginFetchError(f"failed to expand marketplace archive: {exc}") from exc

    marketplace_json_path = ".claude-plugin/marketplace.json"
    raw = archive_files.get(marketplace_json_path)
    if raw is None:
        raise PluginFetchError(f"{marketplace_json_path} not found in marketplace archive")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PluginFetchError(f"marketplace.json is not valid JSON: {exc}") from exc

    # Validate required fields
    if not isinstance(data, dict):
        raise PluginFetchError("marketplace.json top level must be an object")
    name = data.get("name")
    if not name or not isinstance(name, str):
        raise PluginFetchError("marketplace.json missing required field 'name'")
    owner_info = data.get("owner")
    if not isinstance(owner_info, dict) or not owner_info.get("name"):
        raise PluginFetchError("marketplace.json missing required field 'owner.name'")
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise PluginFetchError("marketplace.json missing required field 'plugins'")

    return MarketplaceCatalog(
        name=name,
        owner=owner_info,
        plugins=plugins,
        description=data.get("description"),
        version=data.get("version"),
        metadata=data.get("metadata") or {},
        _archive_files=archive_files,
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
        resolved = sha or ref or "HEAD"
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
        resolved = sha or explicit_ref or "HEAD"
        return owner, repo, resolved
    if source_type == "git-subdir":
        url = src.get("url", "")
        if not url:
            raise PluginFetchError(f"plugin '{entry.get('name')}' git-subdir source missing 'url'")
        owner, repo, _parsed_ref = _parse_github_source(url)
        explicit_ref = src.get("ref")
        sha = src.get("sha")
        resolved = sha or explicit_ref or "HEAD"
        return owner, repo, resolved
    raise PluginFetchError(
        f"plugin '{entry.get('name')}' uses unknown source type '{source_type}'; "
        "only GitHub-based sources are supported"
    )


async def _fetch_plugin_archive(entry: dict, catalog: MarketplaceCatalog) -> dict[str, bytes]:
    """Fetch the plugin archive (or slice of the marketplace archive) and return
    a flat map of **plugin-root-relative** file paths to raw bytes.
    """
    src = entry.get("source")
    if isinstance(src, str) and src.startswith("./"):
        # Relative path inside the marketplace archive
        rel = src[2:]  # strip "./"
        plugin_root = catalog.metadata.get("pluginRoot", "")
        prefix = f"{plugin_root.rstrip('/')}/{rel}" if plugin_root else rel
        prefix = prefix.strip("/")
        prefix = f"{prefix}/" if prefix else ""
        files: dict[str, bytes] = {}
        for path, content in catalog._archive_files.items():
            if path.startswith(prefix):
                files[path[len(prefix) :]] = content
        if not files:
            raise PluginFetchError(
                f"plugin '{entry.get('name')}' relative path '{src}' not found in marketplace archive"
            )
        return files

    owner, repo, ref = _resolve_plugin_source(entry, catalog)
    assert ref is not None  # only the "./" sentinel yields None, filtered out above
    archive_url = _build_codeload_url(owner, repo, ref)
    try:
        archive_bytes = await _fetch_url(archive_url)
    except aiohttp.ClientResponseError as exc:
        if exc.status == 404:
            raise PluginFetchError(f"plugin archive not found (404): {archive_url}") from exc
        raise PluginFetchError(f"failed to download plugin archive: {exc}") from exc
    except Exception as exc:
        raise PluginFetchError(f"failed to download plugin archive: {exc}") from exc

    try:
        files = _extract_archive(archive_bytes)
    except PluginFetchError:
        raise
    except Exception as exc:
        raise PluginFetchError(f"failed to expand plugin archive: {exc}") from exc

    # git-subdir: strip the subdirectory prefix if present
    src_dict = src if isinstance(src, dict) else {}
    if src_dict.get("source") == "git-subdir":
        subdir = src_dict.get("path", "").rstrip("/") + "/"
        if subdir and subdir != "/":
            subdir_files: dict[str, bytes] = {}
            for path, content in files.items():
                if path.startswith(subdir):
                    subdir_files[path[len(subdir) :]] = content
            if not subdir_files:
                raise PluginFetchError(
                    f"git-subdir path '{src_dict.get('path')}' not found in plugin archive"
                )
            files = subdir_files
    return files


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


@dataclass(frozen=True)
class PluginContentFiles:
    """Installable marketplace files, partitioned by runtime document kind."""

    skills: dict[str, str]
    agents: dict[str, str]
    skipped_count: int


class InstallablePluginFiles(dict[str, str]):
    """Skill files plus optional object-agent files for plugin installation."""

    def __init__(self, skills: dict[str, str], *, agents: dict[str, str]) -> None:
        super().__init__(skills)
        self.agents = agents


def _extract_object_agents_from_files(
    files: dict[str, bytes],
) -> tuple[dict[str, str], int]:
    """Extract ``agents/objects/<name>/SYSTEM.md`` directories from a plugin."""
    agent_names = {
        parts[2]
        for path in files
        if (parts := path.split("/"))
        and len(parts) == 4
        and parts[0:2] == ["agents", "objects"]
        and parts[3] == "SYSTEM.md"
        and parts[2]
    }
    if not agent_names:
        return {}, 0

    result: dict[str, str] = {}
    skipped = 0
    file_count = 0
    for path, content in files.items():
        for agent_name in agent_names:
            prefix = f"agents/objects/{agent_name}/"
            if not path.startswith(prefix):
                continue
            if file_count >= _MAX_FILES_PER_PLUGIN or len(content) > _MAX_FILE_BYTES:
                skipped += 1
                break
            try:
                result[f"{agent_name}/{path[len(prefix) :]}"] = content.decode("utf-8")
                file_count += 1
            except UnicodeDecodeError:
                skipped += 1
            break
    return result, skipped


def extract_plugin_content_files(files: dict[str, bytes], plugin_name: str) -> PluginContentFiles:
    """Extract skills and object agents while preserving skill-only compatibility."""
    try:
        skills, skipped_skills = _extract_skills_from_files(files, plugin_name)
    except PluginFetchError as exc:
        if str(exc) != "plugin ships no skills":
            raise
        skills, skipped_skills = {}, 0
    agents, skipped_agents = _extract_object_agents_from_files(files)
    if not skills and not agents:
        raise PluginFetchError("plugin ships no installable skills or object agents")
    return PluginContentFiles(
        skills=skills,
        agents=agents,
        skipped_count=skipped_skills + skipped_agents,
    )


async def fetch_plugin_content_files_with_report(
    catalog: MarketplaceCatalog,
    plugin_name: str,
) -> PluginContentFiles:
    """Fetch a plugin archive and split installable skills from object agents."""
    entry: dict[str, Any] | None = None
    for plugin in catalog.plugins:
        if plugin.get("name") == plugin_name:
            entry = plugin
            break
    if entry is None:
        raise PluginFetchError(f"plugin '{plugin_name}' not found in marketplace catalog")
    return extract_plugin_content_files(await _fetch_plugin_archive(entry, catalog), plugin_name)


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
    raise PluginFetchError("plugin ships no skills")


async def fetch_plugin_files_with_report(
    catalog: MarketplaceCatalog,
    plugin_name: str,
    *,
    include_agents: bool = False,
) -> tuple[dict[str, str], int]:
    """Fetch installable plugin files.

    The default retains the public skill-only contract. Installation passes
    ``include_agents=True`` and receives an ``InstallablePluginFiles`` mapping
    whose ``agents`` attribute is keyed as ``<agent-name>/<filename>``.
    """
    entry: dict[str, Any] | None = None
    for p in catalog.plugins:
        if p.get("name") == plugin_name:
            entry = p
            break
    if entry is None:
        raise PluginFetchError(f"plugin '{plugin_name}' not found in marketplace catalog")

    raw_files = await _fetch_plugin_archive(entry, catalog)
    if not include_agents:
        return _extract_skills_from_files(raw_files, plugin_name)
    content = extract_plugin_content_files(raw_files, plugin_name)
    return InstallablePluginFiles(content.skills, agents=content.agents), content.skipped_count


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
