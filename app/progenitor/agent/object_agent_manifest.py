"""Parsing and discovery for manifest-backed object-agent definitions."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

import yaml
from deepagents.middleware.filesystem import FilesystemPermission

from ..knowledge.pages import BASE_ID_RE

logger = logging.getLogger(__name__)

OBJECT_AGENT_KIND = "object"
OBJECT_AGENT_SYSTEM_FILE = "SYSTEM.md"
OBJECT_AGENT_DOCUMENT_KIND = "agent"
NATIVE_OBJECT_AGENT_SOURCE = "native"


DEFAULT_SHELL_MAX_OUTPUT_BYTES: Final = 100_000
DEFAULT_SHELL_SYSTEM_PATH: Final = "/usr/bin:/bin"


@dataclass(frozen=True)
class ObjectAgentShellConfig:
    """Runtime shell settings parsed from flat manifest front matter."""

    max_output_bytes: int
    env_passthrough: tuple[str, ...]
    system_path: str
    # Lets the git/gh shims run repository setup commands (see data/shims/bin).
    git_write: bool = False
    # With git_write: lets the shims push named branches and open pull requests.
    git_publish: bool = False


@dataclass(frozen=True)
class ObjectAgentMount:
    """A virtual route exposed to a manifest-backed object agent."""

    path: str
    access: str


@dataclass(frozen=True)
class ObjectAgentKnowledge:
    base: str
    access: str


_SHELL_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True)
class ObjectAgentManifest:
    """Runtime-supported object-agent front matter."""

    name: str
    description: str
    version: str
    system_prompt: str
    model: str | None
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    permissions: tuple[FilesystemPermission, ...]
    mounts: tuple[ObjectAgentMount, ...]
    interrupt_on: dict[str, bool | dict[str, Any]]
    shell: ObjectAgentShellConfig | None = None
    # Opt-in private memory at /memories/agents/<slug>/AGENTS.md.
    memory: bool = False
    knowledge: tuple[ObjectAgentKnowledge, ...] = ()


@dataclass(frozen=True)
class ObjectAgentSource:
    """One discovered agent definition and its authoritative source."""

    manifest: ObjectAgentManifest
    source: str
    relative_path: str | None = None
    file_path: Path | None = None

    @property
    def name(self) -> str:
        return self.manifest.name


def _require_string(value: Any, *, field: str, source: str) -> str:
    if not isinstance(value, str) or not (result := value.strip()):
        raise ValueError(f"{source}: '{field}' must be a non-empty string")
    return result


def _string_list(value: Any, *, field: str, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{source}: '{field}' must be an array of non-empty strings")
    return tuple(item.strip() for item in value)


def _parse_permissions(value: Any, *, source: str) -> tuple[FilesystemPermission, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{source}: 'permissions' must be an array")

    result: list[FilesystemPermission] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{source}: permissions[{index}] must be an object")
        operations = entry.get("operations")
        paths = entry.get("paths")
        mode = entry.get("mode", "allow")
        if (
            not isinstance(operations, list)
            or not operations
            or any(operation not in {"read", "write"} for operation in operations)
        ):
            raise ValueError(
                f"{source}: permissions[{index}].operations must contain read and/or write"
            )
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(path, str) and path for path in paths)
        ):
            raise ValueError(f"{source}: permissions[{index}].paths must be non-empty strings")
        if mode not in {"allow", "deny"}:
            raise ValueError(f"{source}: permissions[{index}].mode must be allow or deny")
        result.append(
            FilesystemPermission(
                operations=list(operations),
                paths=list(paths),
                mode=mode,
            )
        )
    return tuple(result)


def _canonical_virtual_path(value: Any, *, field: str, source: str) -> str:
    """Validate a virtual backend path, never a host filesystem path."""
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise ValueError(f"{source}: '{field}' must be an absolute virtual path")
    path = PurePosixPath(value)
    if ".." in path.parts or str(path) != value.rstrip("/") and value != "/":
        raise ValueError(f"{source}: '{field}' must be a canonical virtual path without traversal")
    # A mount names a directory route.  Normalise its trailing slash so route
    # containment is unambiguous, while permissions may name files or dirs.
    return str(path) + ("/" if field.startswith("mounts") and str(path) != "/" else "")


def _parse_mounts(value: Any, *, source: str) -> tuple[ObjectAgentMount, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{source}: 'mounts' must be an array")
    mounts: list[ObjectAgentMount] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{source}: mounts[{index}] must be an object")
        path = _canonical_virtual_path(
            entry.get("path"), field=f"mounts[{index}].path", source=source
        )
        access = entry.get("access")
        if access not in {"read", "read-write"}:
            raise ValueError(f"{source}: mounts[{index}].access must be read or read-write")
        mounts.append(ObjectAgentMount(path=path, access=access))
    if len({mount.path for mount in mounts}) != len(mounts):
        raise ValueError(f"{source}: mounts must not contain duplicate paths")
    return tuple(mounts)


def _path_is_mounted(path: str, mounts: Iterable[ObjectAgentMount]) -> bool:
    normalized = _canonical_virtual_path(path, field="permissions.paths", source="manifest")
    return any(
        normalized.startswith(mount.path) or normalized == mount.path.rstrip("/")
        for mount in mounts
    )


def _validate_permissions_within_mounts(
    permissions: Iterable[FilesystemPermission], mounts: Iterable[ObjectAgentMount], *, source: str
) -> None:
    mount_list = tuple(mounts)
    for rule in permissions:
        for path in rule.paths:
            if not _path_is_mounted(path, mount_list):
                raise ValueError(f"{source}: permissions path {path!r} is outside declared mounts")


def skill_mounts(skills: Iterable[str]) -> tuple[ObjectAgentMount, ...]:
    """Each listed skill is an implicit read-only mount of exactly its own folder."""
    return tuple(
        ObjectAgentMount(path=f"{PurePosixPath(skill)}/", access="read")
        for skill in dict.fromkeys(skills)
    )


def implicit_mounts(
    skills: Iterable[str],
    *,
    memory: bool,
    knowledge: Iterable[ObjectAgentKnowledge] = (),
) -> tuple[ObjectAgentMount, ...]:
    """Mounts an agent gets without declaring them."""
    memory_mount = (ObjectAgentMount(path="/memories/", access="read-write"),) if memory else ()
    knowledge_mounts = tuple(
        ObjectAgentMount(
            path=f"/knowledge/{grant.base}/",
            access="read-write" if grant.access == "write" else "read",
        )
        for grant in knowledge
    )
    return (*skill_mounts(skills), *memory_mount, *knowledge_mounts)


def _parse_shell(
    value: Any,
    *,
    env_passthrough_value: Any,
    has_env_passthrough: bool,
    git_write_value: Any,
    git_publish_value: Any,
    source: str,
    source_kind: str | None = None,
) -> ObjectAgentShellConfig | None:
    if value is None:
        enabled = False
    elif isinstance(value, bool):
        enabled = value
    else:
        raise ValueError(f"{source}: 'shell' must be a boolean")
    flags: dict[str, bool] = {}
    for name, flag_value in (("git_write", git_write_value), ("git_publish", git_publish_value)):
        if flag_value is None:
            flags[name] = False
        elif isinstance(flag_value, bool):
            flags[name] = flag_value
        else:
            raise ValueError(f"{source}: '{name}' must be a boolean")
    git_write, git_publish = flags["git_write"], flags["git_publish"]

    env_passthrough = _string_list(env_passthrough_value, field="env_passthrough", source=source)
    if any(_SHELL_ENV_NAME.fullmatch(name) is None for name in env_passthrough):
        raise ValueError(
            f"{source}: env_passthrough entries must be upper-case environment variable names"
        )
    if source_kind is not None and source_kind != "native":
        if enabled or has_env_passthrough or git_write or git_publish:
            raise ValueError(
                f"{source}: shell, env_passthrough, git_write and git_publish are only "
                "allowed for native agents"
            )
        return None
    if not enabled:
        if has_env_passthrough:
            raise ValueError(f"{source}: env_passthrough requires shell: true")
        for name, flag in (("git_write", git_write), ("git_publish", git_publish)):
            if flag:
                raise ValueError(f"{source}: {name} requires shell: true")
        return None
    if git_publish and not git_write:
        raise ValueError(f"{source}: git_publish requires git_write: true")

    return ObjectAgentShellConfig(
        max_output_bytes=DEFAULT_SHELL_MAX_OUTPUT_BYTES,
        env_passthrough=env_passthrough,
        system_path=DEFAULT_SHELL_SYSTEM_PATH,
        git_write=git_write,
        git_publish=git_publish,
    )


def _parse_interrupt_on(value: Any, *, source: str) -> dict[str, bool | dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{source}: 'interrupt_on' must be an object keyed by tool name")

    result: dict[str, bool | dict[str, Any]] = {}
    for tool_name, config in value.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(f"{source}: interrupt_on keys must be non-empty tool names")
        if not isinstance(config, (bool, dict)):
            raise ValueError(f"{source}: interrupt_on.{tool_name} must be a boolean or object")
        result[tool_name.strip()] = config
    return result


def parse_front_matter(content: str, *, source: str) -> dict[str, Any]:
    """Parse the raw YAML front matter of a ``SYSTEM.md`` document.

    Returns every key present, including ones the runtime manifest ignores --
    this is what lets callers (e.g. the Agents page) represent the literal
    file rather than a fixed, hardcoded field set.
    """
    if not content.startswith("---"):
        raise ValueError(f"{source}: SYSTEM.md must begin with YAML front matter")
    try:
        _, front_matter, _ = content.split("---", 2)
    except ValueError as exc:
        raise ValueError(f"{source}: SYSTEM.md must close YAML front matter with ---") from exc
    try:
        raw = yaml.safe_load(front_matter)
    except yaml.YAMLError as exc:
        raise ValueError(f"{source}: invalid YAML front matter: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError(f"{source}: YAML front matter must be an object")
    return dict(raw)


def _parse_memory(value: Any, *, source: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"{source}: 'memory' must be a boolean")
    return value


def _parse_knowledge(value: Any, *, source: str) -> tuple[ObjectAgentKnowledge, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{source}: 'knowledge' must be an array")
    grants: list[ObjectAgentKnowledge] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{source}: knowledge[{index}] must be an object")
        base = entry.get("base")
        access = entry.get("access")
        if not isinstance(base, str) or BASE_ID_RE.fullmatch(base) is None:
            raise ValueError(f"{source}: knowledge[{index}].base must be a lowercase kebab-case id")
        if access not in {"read", "write"}:
            raise ValueError(f"{source}: knowledge[{index}].access must be read or write")
        grants.append(ObjectAgentKnowledge(base=base, access=access))
    if len({grant.base for grant in grants}) != len(grants):
        raise ValueError(f"{source}: knowledge must not contain duplicate bases")
    return tuple(grants)


def parse_object_agent_manifest(
    content: str, *, source: str, source_kind: str | None = None
) -> ObjectAgentManifest:
    """Parse a ``SYSTEM.md`` document without applying JSON Schema validation."""
    raw = parse_front_matter(content, source=source)
    system_prompt = content.split("---", 2)[2]
    name = _require_string(raw.get("name"), field="name", source=source)
    description = _require_string(raw.get("description"), field="description", source=source)
    version = _require_string(raw.get("version"), field="version", source=source)
    if raw.get("kind") != OBJECT_AGENT_KIND:
        raise ValueError(f"{source}: 'kind' must be '{OBJECT_AGENT_KIND}'")
    body = system_prompt.strip()
    if not body:
        raise ValueError(f"{source}: SYSTEM.md must contain a Markdown system prompt")

    model = raw.get("model")
    if model is not None:
        model = _require_string(model, field="model", source=source)
    skills = _string_list(raw.get("skills"), field="skills", source=source)
    if any(not skill.startswith("/skills/") or ".." in Path(skill).parts for skill in skills):
        raise ValueError(f"{source}: skills must be absolute paths below /skills/")
    if any(PurePosixPath(skill) == PurePosixPath("/skills") for skill in skills):
        raise ValueError(
            f"{source}: skills entries name one skill folder (/skills/<name>); "
            "use mounts for access to all skills"
        )

    mounts = _parse_mounts(raw.get("mounts"), source=source)
    if any(m.path.startswith("/knowledge/") for m in mounts):
        raise ValueError(f"{source}: grant knowledge bases with the 'knowledge' field, not mounts")
    # /config/ holds runtime configuration (mcp.json adds MCP servers, agents.json
    # the A2A registry); only bundled agents may change it, as with shell.
    writable_config = any(
        m.access == "read-write" and m.path.startswith("/config/") for m in mounts
    )
    if writable_config and source_kind is not None and source_kind != "native":
        raise ValueError(f"{source}: read-write /config/ mounts are only allowed for native agents")
    memory = _parse_memory(raw.get("memory"), source=source)
    knowledge = _parse_knowledge(raw.get("knowledge"), source=source)
    # memory: true makes /memories/ the agent's private folder, so it can't
    # also mount the shared one.
    if memory and any(m.path.startswith("/memories/") for m in mounts):
        raise ValueError(
            f"{source}: memory: true uses /memories/ for the agent's private memory; "
            "remove /memories/ mounts"
        )
    permissions = _parse_permissions(raw.get("permissions"), source=source)
    # Manifests written before scoped mounts existed remain valid.  They have
    # no filesystem surface now; once a manifest declares mounts every
    # operation-level permission must be contained by one of them (each
    # listed skill counts as a read mount of its folder, memory as /memories/).
    if mounts or knowledge:
        _validate_permissions_within_mounts(
            permissions,
            (*mounts, *implicit_mounts(skills, memory=memory, knowledge=knowledge)),
            source=source,
        )

    return ObjectAgentManifest(
        name=name,
        description=description,
        version=version,
        system_prompt=body,
        model=model,
        tools=_string_list(raw.get("tools"), field="tools", source=source),
        skills=skills,
        permissions=permissions,
        mounts=mounts,
        interrupt_on=_parse_interrupt_on(raw.get("interrupt_on"), source=source),
        shell=_parse_shell(  # nosec B604 - `shell` here is the ObjectAgentManifest field name, not subprocess.run's shell= kwarg
            raw.get("shell"),
            env_passthrough_value=raw.get("env_passthrough"),
            has_env_passthrough="env_passthrough" in raw,
            git_write_value=raw.get("git_write"),
            git_publish_value=raw.get("git_publish"),
            source=source,
            source_kind=source_kind,
        ),
        memory=memory,
        knowledge=knowledge,
    )


def discover_native_object_agents(native_root: Path) -> list[ObjectAgentSource]:
    """Read bundled definitions directly; never copy or mutate them."""
    root = Path(native_root)
    if not root.is_dir():
        return []
    sources = []
    for agent_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        system_path = agent_dir / OBJECT_AGENT_SYSTEM_FILE
        if not system_path.is_file():
            continue
        try:
            content = system_path.read_text(encoding="utf-8")
            manifest = parse_object_agent_manifest(
                content, source=str(system_path), source_kind="native"
            )
        except (OSError, ValueError) as exc:
            logger.warning("Skipping invalid native agent %s: %s", system_path, exc)
            continue
        sources.append(
            ObjectAgentSource(
                manifest=manifest, source=NATIVE_OBJECT_AGENT_SOURCE, file_path=system_path
            )
        )
    return sources


async def discover_object_agents(
    state_manager: Any,
    native_root: Path | None = None,
) -> list[ObjectAgentSource]:
    """Combine read-only bundled definitions with live Postgres definitions."""
    by_name: dict[str, ObjectAgentSource] = {}
    for source in discover_native_object_agents(native_root) if native_root is not None else []:
        by_name.setdefault(source.name, source)
    documents = await state_manager.list_content_documents(OBJECT_AGENT_DOCUMENT_KIND)
    for relative_path, content in sorted(
        documents, key=lambda row: (not row[0].startswith("native/"), row[0])
    ):
        parts = relative_path.split("/")
        if len(parts) != 3 or parts[-1] != OBJECT_AGENT_SYSTEM_FILE or parts[0] == "native":
            continue
        source_label = f"marketplace:{parts[0]}" if "__" in parts[0] else "user"
        try:
            manifest = parse_object_agent_manifest(
                content, source=f"agent:{relative_path}", source_kind=source_label
            )
        except ValueError as exc:
            logger.warning("Skipping invalid object agent document %s: %s", relative_path, exc)
            continue
        if manifest.name in by_name:
            logger.warning(
                "Skipping object agent %r from %s because %s already defines it",
                manifest.name,
                relative_path,
                by_name[manifest.name].source,
            )
            continue
        by_name[manifest.name] = ObjectAgentSource(
            manifest=manifest,
            source=source_label,
            relative_path=relative_path,
        )
    return sorted(by_name.values(), key=lambda item: item.name)
