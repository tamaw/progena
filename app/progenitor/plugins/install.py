"""Event-driven reconciler for installed plugins.

Reads ``/config/plugins.json``, fetches plugin skill files from their
marketplace sources, and materialises/deletes the corresponding
``skill:`` content documents.  Also performs periodic upstream update
checks (digest comparison) without auto-applying.
"""

from __future__ import annotations

import logging
from typing import Any

from ..utils import utcnow_isoformat
from .sources import (
    MarketplaceCatalog,
    PluginFetchError,
    _parse_github_source,
    compute_digest,
    fetch_marketplace,
    fetch_plugin_files_with_report,
    resolve_plugin_version,
)

logger = logging.getLogger(__name__)


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
) -> tuple[dict[str, str], str]:
    """Fetch a plugin's skill files and compute their digest."""
    if catalog is None:
        catalog = _build_synthetic_catalog(entry, plugin_name, marketplace_name)
    files, _skipped = await fetch_plugin_files_with_report(catalog, plugin_name)
    return files, compute_digest(files)


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
            if not isinstance(marketplace_source, str) or not marketplace_source:
                raise PluginFetchError(f"plugin '{key}' has no marketplace provenance")
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
            current = dict(entry)
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


async def reconcile_plugins(state_manager, *, log_sink=None) -> dict[str, Any]:
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

    actual_rows = await state_manager.list_content_documents("skill")
    actual_by_segment: dict[str, list[str]] = {}
    for rel, _content in actual_rows:
        seg = rel.split("/", 1)[0] if "/" in rel else rel
        actual_by_segment.setdefault(seg, []).append(rel)

    summary: dict[str, list[str]] = {
        "installed": [],
        "removed": [],
        "updated_available": [],
        "errored": [],
    }
    changed = False
    now = utcnow_isoformat()

    # --- 1. Remove skill documents for plugins no longer in desired state ---
    # Progenitor's own native skills are disk-only now (never seeded into
    # Postgres, see agent.py) -- any unsegmented ("no __") row still in the
    # store is leftover from before that change and is safe to delete too.
    desired_segments = {_plugin_segment(k) for k in plugins}
    for seg, rels in list(actual_by_segment.items()):
        if seg in desired_segments:
            continue
        for rel in rels:
            await state_manager.delete_content_document("skill", rel)
        summary["removed"].append(seg)
        changed = True

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
            files, digest = await _fetch_and_digest(current, plugin_name, marketplace_name)
            resolved_version = resolve_plugin_version(current, None)

            if needs_install:
                # Write skill documents
                for path, content in files.items():
                    # path is like "skill-name/SKILL.md"
                    await state_manager.save_content_document("skill", f"{seg}/{path}", content)

                skills = sorted({p.split("/", 1)[0] for p in files if "/" in p})
                current["installed_version"] = desired_version or resolved_version
                current["installed_digest"] = digest
                current["skills"] = skills
                current["status"] = "installed"
                current["status_message"] = ""
                current["installed_at"] = current.get("installed_at") or now
                current["last_checked"] = now
                current["available_version"] = None
                summary["installed"].append(key)
                changed = True
            else:
                # Frozen or already up-to-date — just check for updates
                if digest != current.get("installed_digest"):
                    current["available_version"] = resolved_version
                    summary["updated_available"].append(key)
                    changed = True
                current["last_checked"] = now

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
        if log_sink is not None:
            for key in (
                summary["installed"]
                + summary["removed"]
                + summary["updated_available"]
                + summary["errored"]
            ):
                await log_sink.write_log(
                    source="plugins",
                    message=f"Plugin {key} reconciled",
                    payload={"plugin": key, "summary": summary},
                    timestamp=now,
                    relative_path="config/plugins",
                )
    else:
        # Return empty summary on no-op to signal idempotence
        summary = {"installed": [], "removed": [], "updated_available": [], "errored": []}

    return summary
