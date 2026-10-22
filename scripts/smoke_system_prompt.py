#!/usr/bin/env python
"""Smoke-check the progenitor/SYSTEM.md system prompt against an in-memory store.

Runs the real ``sync_instruction_documents``, ``render_instruction`` and
``LiveInstructionMiddleware`` from this checkout. The content store is a plain
in-memory object and the instruction source is a temporary copy of the bundled
SYSTEM.md, removed on exit. No Postgres, network, provider or credentials.

Usage (repository root, project venv active)::

    python scripts/smoke_system_prompt.py

Prints one PASS line per check and exits nonzero on the first failure.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from langchain.agents.middleware.types import ModelRequest  # noqa: E402
from langchain_core.messages import SystemMessage  # noqa: E402

import src.progenitor.agent.middleware.live_instruction as live_instruction  # noqa: E402
import src.progenitor.documents.content as content  # noqa: E402
import src.progenitor.instructions.documents as instruction_documents  # noqa: E402

SYSTEM = instruction_documents.SYSTEM_INSTRUCTION
LEGACY = "progenitor/genesis.md"
BUNDLED = REPO_ROOT / "data" / "instructions" / "SYSTEM.md"
BASE_PROMPT = "\n\nBase agent prompt."


class MemoryContentStore:
    """Async instruction store with the list/get/save/delete content calls."""

    def __init__(self, docs: dict[str, str] | None = None) -> None:
        self.docs = dict(docs or {})

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        assert kind == "instruction", kind
        return list(self.docs.items())

    async def get_content_document(self, kind: str, path: str) -> str | None:
        assert kind == "instruction", kind
        return self.docs.get(path)

    async def save_content_document(self, kind: str, path: str, text: str) -> None:
        assert kind == "instruction", kind
        self.docs[path] = text

    async def delete_content_document(self, kind: str, path: str) -> None:
        assert kind == "instruction", kind
        self.docs.pop(path, None)


class SmokeFailure(AssertionError):
    pass


def check(name: str, condition: bool, detail: Any = "") -> None:
    if not condition:
        raise SmokeFailure(f"FAIL {name}: {detail}")
    print(f"PASS {name}")


def edit(source: str, marker: str) -> str:
    """A distinct user edit: ``source`` with ``marker`` appended to the body."""
    return f"{source.rstrip()}\n\n{marker}\n"


async def next_call_system_text(middleware: Any, system: SystemMessage) -> str:
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> object:
        seen.append(request)
        return object()

    request = ModelRequest(
        model=None,  # type: ignore[arg-type]  # no provider: the handler never calls it
        messages=[],
        tools=[],
        system_message=system,
    )
    await middleware.awrap_model_call(request, handler)
    sent = seen[0].system_message
    if sent is None:
        raise SmokeFailure("FAIL next-call: handler received no system message")
    return "".join(block["text"] for block in sent.content_blocks if block["type"] == "text")


async def run(source_dir: Path) -> None:
    bundled = (source_dir / "SYSTEM.md").read_text(encoding="utf-8")
    legacy_edit = edit(bundled, "legacy edit")

    store = MemoryContentStore({LEGACY: legacy_edit})
    await content.sync_instruction_documents(store, source_dir)
    check(
        "legacy-genesis-migrates-with-exact-text",
        store.docs.get(SYSTEM) == legacy_edit and LEGACY not in store.docs,
        sorted(store.docs),
    )

    after_first = dict(store.docs)
    await content.sync_instruction_documents(store, source_dir)
    check("second-sync-is-idempotent", store.docs == after_first, sorted(store.docs))

    current = edit(bundled, "current")
    other_legacy = edit(bundled, "other legacy")
    conflict = MemoryContentStore({SYSTEM: current, LEGACY: other_legacy})
    await content.sync_instruction_documents(conflict, source_dir)
    check(
        "existing-system-stays-authoritative-and-both-texts-kept",
        conflict.docs == {SYSTEM: current, LEGACY: other_legacy},
        conflict.docs.keys(),
    )

    boot_text = instruction_documents.render_instruction(store.docs[SYSTEM]).strip()
    middleware = live_instruction.LiveInstructionMiddleware(store, SYSTEM, boot_text)
    system = SystemMessage(content_blocks=[{"type": "text", "text": boot_text + BASE_PROMPT}])
    loaded = await next_call_system_text(middleware, system)
    check("initial-load-sends-migrated-prompt", loaded == boot_text + BASE_PROMPT, loaded[:80])

    later = edit(bundled, "next-call edit")
    store.docs[SYSTEM] = later
    expected = instruction_documents.render_instruction(later).strip() + BASE_PROMPT
    sent = await next_call_system_text(middleware, system)
    check(
        "next-call-uses-edited-system-and-keeps-base-prompt",
        sent == expected and "next-call edit" in sent and sent.endswith(BASE_PROMPT),
        sent[:80],
    )


def main() -> int:
    for module in (content, instruction_documents, live_instruction):
        print(f"module {module.__name__}: {module.__file__}")
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    print(f"commit: {commit.stdout.strip() or 'unknown'}")

    scratch = Path(tempfile.mkdtemp(prefix="smoke-system-prompt-"))
    try:
        shutil.copyfile(BUNDLED, scratch / "SYSTEM.md")
        asyncio.run(run(scratch))
    except SmokeFailure as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if scratch.exists():
        print(f"FAIL scratch-removed: {scratch}", file=sys.stderr)
        return 1
    print("PASS scratch-removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
