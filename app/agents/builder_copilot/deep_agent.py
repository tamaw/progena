"""DeepAgent factory for the Builder Copilot agent."""

from __future__ import annotations

import logging
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from .config import BuilderCopilotConfig, get_config
from .langchain_adapter import create_langchain_model
from .middleware import (
    AuditLoggingMiddleware,
    MetricsMiddleware,
    SecretRedactionMiddleware,
)
from .tools import create_builder_copilot_tools

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are Builder Copilot, a minimal build agent for repository changes.

Work in this order unless the user explicitly tells you otherwise:
1. plan
2. build
3. test
4. lint
5. pr

Rules:
- Start by normalizing the request, then create a worktree unless one is already provided.
- Use python_search to inspect the codebase before major edits.
- Use run_copilot_build for implementation work and run_worktree_command for validation.
- Run both test and lint steps before opening a PR unless the user explicitly asks to skip them.
- Commit changes before calling create_pull_request.
- Return a concise final summary that includes the worktree path, commands run, and PR URL when available.
""".strip()


def create_builder_copilot_agent(
    *,
    config: BuilderCopilotConfig | None = None,
    project_root: str | Path | None = None,
    log_dir: str | Path | None = None,
) -> CompiledStateGraph:
    """Create a minimal DeepAgent for builder-copilot."""
    config = config or get_config(validate=False)
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]

    model = create_langchain_model(config.llm, role="builder")
    backend = LocalShellBackend(
        root_dir=root,
        virtual_mode=False,
        inherit_env=True,
        timeout=min(config.copilot.timeout_seconds, 1200),
    )
    skills_root = (Path(__file__).resolve().parent / "skills").relative_to(root).as_posix()
    tools = create_builder_copilot_tools(
        config=config,
        project_root=root,
        log_dir=log_dir,
    )
    middleware = [
        SecretRedactionMiddleware(),
        AuditLoggingMiddleware(log_dir=log_dir),
        MetricsMiddleware(),
    ]

    agent = create_deep_agent(
        name="builder-copilot",
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        skills=[skills_root],
        backend=backend,
        middleware=middleware,
        checkpointer=MemorySaver(),
    )
    logger.info("Created Builder Copilot DeepAgent with skills root %s", skills_root)
    return agent
