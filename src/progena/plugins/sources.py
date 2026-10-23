"""Fetch and parse marketplace catalogs and plugin skill files."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import aiohttp
import yaml

# ---------------------------------------------------------------------------
# SSRF hardening (mirrors src/backend/attachments/processing.py:44-63,490-493)
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


async def _fetch_url(url: str) -> bytes:
    """SSRF-hardened GET that follows redirects explicitly.

    Mirrors the hardening in src/backend/attachments/processing.py:
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


async def _fetch_github_tree(
    owner: str,
    repo: str,
    sha: str,
    prefix: str,
    *,
    load_files: bool = True,
) -> dict[str, bytes]:
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
        if (
            relative.startswith("skills/")
            or relative == "skills"
            or relative.startswith("agents/objects/")
            or relative == "agents/objects"
        ):
            files[relative] = (
                await _fetch_url(_raw_github_url(owner, repo, sha, path)) if load_files else b""
            )
    return files


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------
_MARKETPLACE_MANIFEST_PATHS = (
    ".claude-plugin/marketplace.json",
    ".github/plugin/marketplace.json",
)


async def fetch_marketplace(
    source: str,
    *,
    resolve_revision: bool = True,
) -> MarketplaceCatalog:
    """Fetch and parse a marketplace manifest over direct HTTP."""
    owner, repo, ref = _parse_github_source(source)
    resolved_ref = ref or "main"
    commit_sha = await _resolve_commit_sha(owner, repo, resolved_ref) if resolve_revision else None
    manifest_ref = commit_sha or resolved_ref
    data: dict[str, Any] | None = None
    for manifest_path in _MARKETPLACE_MANIFEST_PATHS:
        try:
            raw = await _fetch_url(_raw_github_url(owner, repo, manifest_ref, manifest_path))
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


async def _fetch_plugin_archive(
    entry: dict,
    catalog: MarketplaceCatalog,
    *,
    load_files: bool = True,
) -> dict[str, bytes]:
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
    return await _fetch_github_tree(owner, repo, sha, plugin_root, load_files=load_files)


async def list_plugin_skill_names(catalog: MarketplaceCatalog, plugin_name: str) -> list[str]:
    entry = next((p for p in catalog.plugins if p.get("name") == plugin_name), None)
    if entry is None:
        raise PluginFetchError(f"plugin '{plugin_name}' not found")
    files = await _fetch_plugin_archive(entry, catalog, load_files=False)
    names = {
        (path.removeprefix("skills/").split("/", 1)[0])
        for path in files
        if path.endswith("/SKILL.md") and "/" in path
    }
    return sorted(name for name in names if name)


async def fetch_plugin_skill_content(
    catalog: MarketplaceCatalog,
    plugin_name: str,
    skill_name: str,
) -> str:
    """Fetch one marketplace SKILL.md directly at the catalog revision."""
    entry = next((p for p in catalog.plugins if p.get("name") == plugin_name), None)
    if entry is None:
        raise PluginFetchError(f"plugin '{plugin_name}' not found")
    src = entry.get("source")
    if isinstance(src, dict) and src.get("source") in {"github", "git-subdir"}:
        owner, repo, source_ref = _parse_github_source(str(src.get("repo") or src.get("url") or ""))
        source_ref = str(src.get("ref") or source_ref or "main")
    elif catalog.repository:
        owner, repo = catalog.repository.split("/", 1)
        source_ref = "main"
    else:
        raise PluginFetchError("marketplace catalog is missing its repository")
    root = ""
    if isinstance(src, str):
        root = src.removeprefix("./").strip("/")
    elif isinstance(src, dict):
        root = str(src.get("path") or "").strip("/")
    path = "/".join(part for part in (root, "skills", skill_name, "SKILL.md") if part)
    sha = (
        catalog.commit_sha
        if catalog.repository == f"{owner}/{repo}" and catalog.commit_sha
        else await _resolve_commit_sha(owner, repo, source_ref)
    )
    try:
        raw = await _fetch_url(_raw_github_url(owner, repo, sha, path))
    except aiohttp.ClientResponseError as exc:
        if exc.status == 404:
            raise PluginFetchError(
                f"skill '{skill_name}' not found for plugin '{plugin_name}'"
            ) from exc
        raise
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginFetchError(f"skill '{skill_name}' is not valid UTF-8") from exc


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
    # A marketplace plugin without a skills/ directory is valid but provides
    # no Progena skills.
    return {}, 0


async def fetch_plugin_files_with_report(
    catalog: MarketplaceCatalog,
    plugin_name: str,
) -> tuple[dict[str, str], int]:
    """Fetch skill files and their skipped count for skill-only callers.

    Installation uses :func:`fetch_plugin_content_files_with_report` to
    receive skills and object agents as explicit fields.
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
