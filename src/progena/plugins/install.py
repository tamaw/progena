"""Event-driven reconciler for installed plugins.

Reads ``/config/plugins.json``, fetches plugin skill files from their
marketplace sources, and materialises/deletes the corresponding
``skill:`` content documents.  Also performs periodic upstream update
checks (digest comparison) without auto-applying.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..utils import utcnow_isoformat
from .sources import (
    MarketplaceCatalog,
    _parse_github_source,
    compute_digest,
    fetch_marketplace,
    fetch_plugin_content_files_with_report,
    resolve_plugin_version,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidatedPluginContent:
    """Valid installable content, its combined digest and skipped agent names."""

    skills: dict[str, str]
    agents: dict[str, str]
    digest: str
    skipped_agents: list[str]


def _plugin_segment(key: str) -> str:
    """Convert ``<marketplace>/<plugin>`` → ``<marketplace>__<plugin>``."""
    return key.replace("/", "__", 1)


def _build_synthetic_catalog(
    entry: dict, plugin_name: str, marketplace_name: str
) -> MarketplaceCatalog:
    """Create a pinned catalog for an installed plugin's direct HTTP fetch."""
    source = entry.get("source")
    source_url = entry.get("marketplace_source")
    if not isinstance(source_url, str):
        source_url = source.get("url") or source.get("repo") if isinstance(source, dict) else ""
    repository: str | None = None
    if isinstance(source_url, str) and source_url:
        owner, repo, _ = _parse_github_source(source_url)
        repository = f"{owner}/{repo}"
    source_dict = source if isinstance(source, dict) else {}
    return MarketplaceCatalog(
        name=marketplace_name,
        owner={},
        plugins=[
            {
                "name": plugin_name,
                "source": source,
                "version": entry.get("version"),
            }
        ],
        repository=repository,
        commit_sha=entry.get("marketplace_commit_sha") or source_dict.get("sha"),
    )


async def _fetch_and_digest(
    entry: dict,
    plugin_name: str,
    marketplace_name: str,
    catalog: MarketplaceCatalog | None = None,
) -> ValidatedPluginContent:
    """Fetch valid skills/object agents and compute one content digest.

    Invalid object-agent manifests are filtered out instead of failing an
    otherwise usable plugin. The returned skipped names are surfaced as an
    installed status warning by the reconciler.
    """
    if catalog is None:
        catalog = _build_synthetic_catalog(entry, plugin_name, marketplace_name)
    files = await fetch_plugin_content_files_with_report(catalog, plugin_name)
    skills = files.skills
    valid_agents, skipped_agents = _filter_valid_agent_files(files.agents, plugin_name)
    if not skills and not valid_agents:
        suffix = (
            f"; skipped invalid object agents: {', '.join(skipped_agents)}"
            if skipped_agents
            else ""
        )
        raise ValueError(f"plugin ships no valid installable skills or object agents{suffix}")
    digest = compute_digest(
        {
            **{f"skills/{path}": content for path, content in skills.items()},
            **{f"agents/{path}": content for path, content in valid_agents.items()},
        }
    )
    return ValidatedPluginContent(
        skills=skills, agents=valid_agents, digest=digest, skipped_agents=skipped_agents
    )


def _filter_valid_agent_files(
    files: dict[str, str], plugin_name: str
) -> tuple[dict[str, str], list[str]]:
    """Keep complete, valid object-agent directories and skip bad ones."""
    from src.progena.agent.object_agents.manifest import parse_object_agent_manifest

    by_agent: dict[str, dict[str, str]] = {}
    for path, content in files.items():
        agent_name, separator, relative = path.partition("/")
        if not separator or not agent_name or not relative:
            continue
        by_agent.setdefault(agent_name, {})[relative] = content

    valid: dict[str, str] = {}
    skipped: list[str] = []
    for agent_name, agent_files in sorted(by_agent.items()):
        system_prompt = agent_files.get("SYSTEM.md")
        if system_prompt is None:
            skipped.append(agent_name)
            logger.warning(
                "Skipping object agent %r from plugin %r: missing SYSTEM.md",
                agent_name,
                plugin_name,
            )
            continue
        try:
            parse_object_agent_manifest(
                system_prompt,
                source=f"plugin:{plugin_name}/agents/objects/{agent_name}/SYSTEM.md",
                source_kind="marketplace",
            )
        except ValueError as exc:
            skipped.append(agent_name)
            logger.warning(
                "Skipping invalid object agent %r from plugin %r: %s",
                agent_name,
                plugin_name,
                exc,
            )
            continue
        for relative, content in agent_files.items():
            valid[f"{agent_name}/{relative}"] = content
    return valid, skipped


async def check_plugin_updates(state_manager) -> dict[str, Any]:
    """Check every installed plugin for upstream changes.

    Only ever sets ``available_version`` and ``last_checked``; never touches
    ``installed_version``, ``skills``, or skill documents.  Called by the
    heartbeat node and reused inside ``reconcile_plugins`` for frozen entries.
    """
    desired = await state_manager.get_plugins_config()
    plugins = desired.get("plugins", {})
    summary: dict[str, list[str]] = {"updated_available": [], "errored": []}
    changed = False
    now = utcnow_isoformat()
    catalogs: dict[str, MarketplaceCatalog] = {}

    for key, entry in list(plugins.items()):
        installed_version = entry.get("installed_version")
        if not installed_version:
            continue
        try:
            marketplace_name, plugin_name = key.split("/", 1)
            marketplace_source = entry.get("marketplace_source")
            current = dict(entry)
            if isinstance(marketplace_source, str) and marketplace_source:
                catalog = catalogs.get(marketplace_source)
                if catalog is None:
                    catalog = await fetch_marketplace(
                        marketplace_source,
                        resolve_revision=False,
                    )
                    catalogs[marketplace_source] = catalog
                current_entry = next(
                    (p for p in catalog.plugins if p.get("name") == plugin_name),
                    None,
                )
                if current_entry is None:
                    current["status"] = "error"
                    current["status_message"] = "No longer listed in marketplace"
                    current["last_checked"] = now
                    plugins[key] = current
                    summary["errored"].append(key)
                    changed = True
                    continue
                version = resolve_plugin_version(current_entry, None)
                if version != installed_version:
                    current["available_version"] = version
                    summary["updated_available"].append(key)
                    changed = True
            else:
                # No marketplace provenance (installed directly from a
                # GitHub/git-subdir source, not via a marketplace listing):
                # fall back to a digest comparison against upstream, same as
                # reconcile_plugins' frozen-entry check.
                content = await _fetch_and_digest(current, plugin_name, marketplace_name)
                if content.digest != current.get("installed_digest"):
                    current["available_version"] = resolve_plugin_version(current, None)
                    summary["updated_available"].append(key)
                    changed = True
            current["last_checked"] = now
            current["status"] = "installed"
            current["status_message"] = ""
            if current != entry:
                plugins[key] = current
        except Exception as exc:
            current = dict(entry)
            message = str(exc)
            status_changed = (
                current.get("status") != "error" or current.get("status_message") != message
            )
            current["status"] = "error"
            current["status_message"] = message
            if status_changed:
                current["last_checked"] = now
                plugins[key] = current
                changed = True
            summary["errored"].append(key)
            logger.warning("Plugin update check failed for %s: %s", key, exc)

    if changed:
        await state_manager.save_plugins_config(desired)

    return summary


async def reconcile_plugins(state_manager) -> dict[str, Any]:
    """Reconcile the desired plugin set against the actual skill documents.

    * Installs new entries (``installed_version`` is null).
    * Upgrades/downgrades when ``version`` is set and differs from
      ``installed_version``.
    * Refreshes ``available_version`` for frozen entries (``version`` unset,
      ``installed_version`` set) via digest comparison.
    * Removes skill documents for deleted entries.
    * Updates runtime-owned fields and persists the ``plugins`` document.
    * Skips ``save_plugins_config`` when nothing changed (idempotent).

    One failing plugin never aborts the pass for others.
    """
    desired = await state_manager.get_plugins_config()
    plugins = desired.get("plugins", {})

    actual_by_kind_segment: dict[str, dict[str, list[str]]] = {}
    for kind in ("skill", "agent"):
        rows = await state_manager.list_content_documents(kind)
        segments: dict[str, list[str]] = {}
        for rel, _content in rows:
            seg = rel.split("/", 1)[0] if "/" in rel else rel
            segments.setdefault(seg, []).append(rel)
        actual_by_kind_segment[kind] = segments

    summary: dict[str, list[str]] = {
        "installed": [],
        "removed": [],
        "updated_available": [],
        "errored": [],
    }
    changed = False
    now = utcnow_isoformat()

    # --- 1. Remove content for plugins no longer in desired state ---
    # Only marketplace namespaces are plugin-owned. Bundled/user definitions
    # must never be removed by plugin reconciliation.
    desired_segments = {_plugin_segment(k) for k in plugins} | {"user"}
    removed_segments: set[str] = set()
    for kind, segments in actual_by_kind_segment.items():
        for seg, rels in list(segments.items()):
            if "__" not in seg or seg in desired_segments:
                continue
            for rel in rels:
                await state_manager.delete_content_document(kind, rel)
            removed_segments.add(seg)
            changed = True
    summary["removed"].extend(sorted(removed_segments))

    # --- 2. Process each desired plugin entry ---
    for key, entry in list(plugins.items()):
        current = dict(entry)
        try:
            # A malformed key (no "/") must fail only this entry, never abort
            # the pass for the others -- everything below, including the
            # split, lives inside the try.
            marketplace_name, plugin_name = key.split("/", 1)
            seg = _plugin_segment(key)

            installed_version = current.get("installed_version")
            desired_version = current.get("version")

            needs_install = installed_version is None or (
                desired_version is not None and desired_version != installed_version
            )
            resolved_version = resolve_plugin_version(current, None)
            if needs_install:
                content = await _fetch_and_digest(current, plugin_name, marketplace_name)
                # Replace the plugin's content as a unit so removed companion
                # files cannot survive an upgrade.
                for kind in ("skill", "agent"):
                    for rel in actual_by_kind_segment[kind].get(seg, []):
                        await state_manager.delete_content_document(kind, rel)
                for path, text in content.skills.items():
                    await state_manager.save_content_document("skill", f"{seg}/{path}", text)
                for path, text in content.agents.items():
                    await state_manager.save_content_document("agent", f"{seg}/{path}", text)

                skills = sorted({p.split("/", 1)[0] for p in content.skills if "/" in p})
                agents = sorted({p.split("/", 1)[0] for p in content.agents if "/" in p})
                current["installed_version"] = desired_version or resolved_version
                current["installed_digest"] = content.digest
                current["skills"] = skills
                current["agents"] = agents
                current["status"] = "installed"
                current["status_message"] = (
                    "Skipped invalid object agents: " + ", ".join(content.skipped_agents)
                    if content.skipped_agents
                    else ""
                )
                current["installed_at"] = current.get("installed_at") or now
                current["last_checked"] = now
                current["available_version"] = None
                summary["installed"].append(key)
                changed = True
            else:
                # Frozen entry (no desired ``version`` bump pending): refresh
                # ``available_version`` via a digest comparison against
                # upstream, without touching installed_version or skills.
                content = await _fetch_and_digest(current, plugin_name, marketplace_name)
                if content.digest != current.get("installed_digest"):
                    current["available_version"] = resolved_version
                    summary["updated_available"].append(key)
                    changed = True
                current["last_checked"] = now
                current["status"] = "installed"
                current["status_message"] = ""

            if current != entry:
                plugins[key] = current
        except Exception as exc:
            current = dict(entry)
            message = str(exc)
            # A last_checked timestamp always differs run-to-run; only treat
            # the error as a *change* (and persist) when the status or its
            # message actually differs from what's already stored. Otherwise
            # a permanently-failing entry would save on every pass, which
            # re-triggers this same reconcile via the store listener --
            # an infinite loop. See check_plugin_updates for the same guard.
            status_changed = (
                current.get("status") != "error" or current.get("status_message") != message
            )
            current["status"] = "error"
            current["status_message"] = message
            if status_changed:
                current["last_checked"] = now
                plugins[key] = current
                changed = True
            summary["errored"].append(key)
            logger.warning("Plugin reconcile failed for %s: %s", key, exc)

    # --- 3. Persist if anything changed ---
    if changed:
        await state_manager.save_plugins_config(desired)
        for key in (
            summary["installed"]
            + summary["removed"]
            + summary["updated_available"]
            + summary["errored"]
        ):
            logger.info("Plugin %s reconciled", key)
    else:
        # Return empty summary on no-op to signal idempotence
        summary = {"installed": [], "removed": [], "updated_available": [], "errored": []}

    return summary
