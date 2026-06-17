"""Per-agent ACP command-line argument builders.

The ACP machinery (``ACPRunner``/``ACPSessionClient``) is agent-agnostic, but each
ACP CLI advertises its ACP mode with its own flags. A registry entry names a
``profile`` (a known agent, e.g. ``"copilot"``) whose builder produces the args,
or supplies raw ``args`` directly.

Adding support for another ACP agent is a matter of writing a builder and adding
it to :data:`ACP_PROFILES` — no changes to the runner are needed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse


def build_copilot_args(
    *,
    model: str | None = None,
    allowed_dirs: Sequence[str] | None = None,
    flags: Sequence[str] | None = None,
) -> list[str]:
    """Build ``copilot`` ACP args: ``--acp --yolo [--model M] [--add-dir D]...``.

    Mirrors ``CopilotACPManager._build_args`` in the Builder Agent
    (``app/agents/builder_agent/runtime/copilot_acp.py``). ``--yolo`` auto-approves
    tool calls; the sandbox ``--add-dir`` is the guardrail.
    """
    args = ["--acp", "--yolo"]
    if model:
        args.extend(["--model", model])
    for extra in flags or []:
        if extra:
            args.append(extra)
    for dir_path in allowed_dirs or []:
        if dir_path:
            args.extend(["--add-dir", dir_path])
    return args


# Known ACP agent profiles. Each builder takes keyword-only model/allowed_dirs/flags
# and returns the CLI args that follow the command. Keep keys lowercase.
ACP_PROFILES: dict[str, Callable[..., list[str]]] = {
    "copilot": build_copilot_args,
}


def parse_acp_connection(connection: str) -> dict[str, Any]:
    """Parse an ACP connection string into ``ACPAgentConfig`` kwargs.

    TCP is the primary mode; stdio is still supported. Examples::

        tcp://192.168.0.2:3000?model=claude-haiku-4.5&cwd=/tmp
        stdio://copilot
        copilot                      # bare command -> stdio

    Recognised query params: ``model``, ``cwd``, ``profile``, ``timeout``.
    Raises ``ValueError`` for an unsupported scheme or a malformed TCP endpoint.
    """
    raw = (connection or "").strip()
    if not raw:
        raise ValueError("empty ACP connection string")

    # Bare command (no scheme) -> local stdio.
    if "://" not in raw and not raw.startswith(("tcp:", "stdio:")):
        return {"command": raw, "profile": _profile_for_command(raw)}

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}

    cfg: dict[str, Any] = {}
    if "model" in query:
        cfg["model"] = query["model"]
    if "cwd" in query:
        cfg["cwd"] = query["cwd"]
    if "timeout" in query:
        try:
            cfg["timeout_seconds"] = int(query["timeout"])
        except ValueError as exc:
            raise ValueError(f"invalid timeout in connection: {query['timeout']!r}") from exc
    profile_q = query.get("profile")

    if scheme == "tcp":
        if not parsed.hostname or not parsed.port:
            raise ValueError(f"TCP connection needs host:port, got {raw!r}")
        cfg["host"] = parsed.hostname
        cfg["port"] = parsed.port
        cfg["profile"] = profile_q or "copilot"
    elif scheme == "stdio":
        command = parsed.netloc or parsed.path.lstrip("/") or "copilot"
        cfg["command"] = command
        cfg["profile"] = profile_q or _profile_for_command(command)
    else:
        raise ValueError(f"unsupported ACP connection scheme: {scheme!r} (use tcp:// or stdio://)")
    return cfg


def _profile_for_command(command: str) -> str | None:
    return "copilot" if PurePosixPath(command).name == "copilot" else None


def build_agent_args(
    profile: str | None,
    *,
    model: str | None = None,
    allowed_dirs: Sequence[str] | None = None,
    flags: Sequence[str] | None = None,
    raw_args: Sequence[str] | None = None,
) -> list[str]:
    """Resolve the CLI args for an ACP agent.

    If ``raw_args`` is given it is used verbatim (advanced/escape hatch). Otherwise
    the named ``profile`` builder is used. An unknown profile falls back to a bare
    ``--acp`` plus any explicit ``flags``/``--add-dir`` entries, which covers most
    spec-compliant stdio agents.
    """
    if raw_args:
        return list(raw_args)
    builder = ACP_PROFILES.get((profile or "").lower())
    if builder is not None:
        return builder(model=model, allowed_dirs=allowed_dirs, flags=flags)
    args = ["--acp"]
    for extra in flags or []:
        if extra:
            args.append(extra)
    for dir_path in allowed_dirs or []:
        if dir_path:
            args.extend(["--add-dir", dir_path])
    return args
