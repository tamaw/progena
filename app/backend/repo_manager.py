"""Managed git repository operations for workflow repo nodes."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.backend.document_store import PROGENITOR_STATE_PREFIX, BackendDocumentStore
from app.progenitor.config import get_config
from app.progenitor.document_kinds import REPO_KEY_PREFIX
from app.progenitor.state.models import redact_secrets

_BRANCH_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


class RepoManager:
    """Execute managed checkout, worktree, and GitHub publishing operations."""

    def __init__(
        self,
        *,
        store_getter: Callable[[], BackendDocumentStore],
        http_transport: httpx.AsyncBaseTransport | None = None,
        managed_root: Path | None = None,
    ) -> None:
        self._store_getter = store_getter
        self._http_transport = http_transport
        self._managed_root_override = managed_root

    async def execute(self, action: str, values: Mapping[str, Any]) -> dict[str, Any]:
        """Perform one repo-node action with an already schema-validated input."""
        repo_id = self._required_string(values, "repo_id")
        entry = self._load_entry(repo_id)
        if action == "sync":
            return await self.sync(repo_id, entry)
        if action == "worktree":
            return await self.worktree(
                repo_id,
                entry,
                branch=self._required_string(values, "branch"),
                base=self._optional_string(values, "base"),
            )
        if action == "publish":
            return await self.publish(
                repo_id,
                entry,
                branch=self._required_string(values, "branch"),
                title=self._required_string(values, "title"),
                body=self._optional_string(values, "body") or "",
            )
        if action == "remove_worktree":
            return await self.remove_worktree(
                repo_id, entry, branch=self._required_string(values, "branch")
            )
        raise ValueError(f"unknown repo action '{action}'")

    async def sync(self, repo_id: str, entry: Mapping[str, Any]) -> dict[str, Any]:
        """Clone a declared repository if missing, else fetch its checkout."""
        path = self._repo_path(entry)
        if path.exists():
            if not path.is_dir() or not await self._is_git_checkout(path):
                raise ValueError(f"repo '{repo_id}' path exists but is not a git checkout: {path}")
            await self._git(path, "fetch", "--prune", use_credentials=True)
        else:
            remote_url = self._remote_url(entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            await self._git(path.parent, "clone", remote_url, str(path), use_credentials=True)
            await self._configure_identity(path)
        default_branch = await self._default_branch(path)
        head = await self._git_stdout(path, "rev-parse", "HEAD")
        return {
            "repo_id": repo_id,
            "path": str(path),
            "default_branch": default_branch,
            "head": head,
        }

    async def worktree(
        self, repo_id: str, entry: Mapping[str, Any], *, branch: str, base: str | None
    ) -> dict[str, Any]:
        """Ensure an isolated worktree exists for a branch."""
        self._require_write_access(repo_id, entry)
        checkout = self._repo_path(entry)
        await self._require_checkout(repo_id, checkout)
        default_branch = await self._default_branch(checkout)
        target = self._worktree_path(repo_id, branch)
        for existing_path, existing_branch in await self._listed_worktrees(checkout):
            if existing_branch == branch:
                head = await self._git_stdout(existing_path, "rev-parse", "HEAD")
                return {
                    "repo_id": repo_id,
                    "branch": branch,
                    "path": str(existing_path),
                    "head": head,
                    "created": False,
                }
        if target.exists():
            raise ValueError(f"worktree path already exists but is not registered: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Never reset an existing branch: it may hold commits not yet pushed.
        if await self._ref_exists(checkout, f"refs/heads/{branch}"):
            await self._git(checkout, "worktree", "add", str(target), branch)
        elif await self._ref_exists(checkout, f"refs/remotes/origin/{branch}"):
            await self._git(
                checkout,
                "worktree",
                "add",
                "--track",
                "-b",
                branch,
                str(target),
                f"origin/{branch}",
            )
        else:
            start_point = base or f"origin/{default_branch}"
            await self._git(checkout, "worktree", "add", "-b", branch, str(target), start_point)
        head = await self._git_stdout(target, "rev-parse", "HEAD")
        return {
            "repo_id": repo_id,
            "branch": branch,
            "path": str(target),
            "head": head,
            "created": True,
        }

    async def remove_worktree(
        self, repo_id: str, entry: Mapping[str, Any], *, branch: str
    ) -> dict[str, Any]:
        """Remove a branch worktree if it exists."""
        self._require_write_access(repo_id, entry)
        checkout = self._repo_path(entry)
        await self._require_checkout(repo_id, checkout)
        target: Path | None = None
        for existing_path, existing_branch in await self._listed_worktrees(checkout):
            if existing_branch == branch:
                target = existing_path
                break
        if target is None:
            return {"repo_id": repo_id, "branch": branch, "removed": False}
        await self._git(checkout, "worktree", "remove", "--force", str(target))
        await self._git(checkout, "worktree", "prune")
        return {"repo_id": repo_id, "branch": branch, "removed": True}

    async def publish(
        self,
        repo_id: str,
        entry: Mapping[str, Any],
        *,
        branch: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        """Push a worktree branch then create or reuse its GitHub pull request."""
        self._require_write_access(repo_id, entry)
        checkout = self._repo_path(entry)
        await self._require_checkout(repo_id, checkout)
        remote = await self._git_stdout(checkout, "remote", "get-url", "origin")
        owner, repository = self._github_repo(remote)
        if owner is None or repository is None:
            raise ValueError(
                f"repo '{repo_id}' origin is not a GitHub remote; cannot publish a pull request"
            )
        token = os.environ.get("GH_TOKEN")
        if not token:
            raise ValueError("GH_TOKEN is required to publish a GitHub pull request")
        worktree = next(
            (
                path
                for path, worktree_branch in await self._listed_worktrees(checkout)
                if worktree_branch == branch
            ),
            None,
        )
        if worktree is None:
            raise ValueError(f"repo '{repo_id}' has no worktree for branch '{branch}'")
        await self._git(worktree, "push", "-u", "origin", branch, use_credentials=True)
        default_branch = await self._default_branch(checkout)
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
        base_url = f"https://api.github.com/repos/{owner}/{repository}/pulls"
        async with httpx.AsyncClient(transport=self._http_transport, timeout=20.0) as client:
            response = await client.get(
                base_url, headers=headers, params={"state": "open", "head": f"{owner}:{branch}"}
            )
            self._raise_api_error(response, token)
            existing = response.json()
            if isinstance(existing, list) and existing:
                pull_request = existing[0]
                pushed = True
            else:
                response = await client.post(
                    base_url,
                    headers=headers,
                    json={"title": title, "body": body, "head": branch, "base": default_branch},
                )
                self._raise_api_error(response, token)
                pull_request = response.json()
                pushed = True
        if not isinstance(pull_request, Mapping):
            raise ValueError("GitHub API returned an invalid pull request response")
        url = pull_request.get("html_url")
        number = pull_request.get("number")
        if not isinstance(url, str) or not isinstance(number, int):
            raise ValueError("GitHub API pull request response is missing html_url or number")
        return {
            "repo_id": repo_id,
            "branch": branch,
            "pushed": pushed,
            "pr_url": url,
            "pr_number": number,
        }

    def _load_entry(self, repo_id: str) -> Mapping[str, Any]:
        document = self._store_getter().get_document(
            PROGENITOR_STATE_PREFIX, f"{REPO_KEY_PREFIX}{repo_id}", None
        )
        payload = getattr(document, "payload", None)
        if not isinstance(payload, Mapping):
            raise ValueError(f"repo '{repo_id}' is not declared")
        return payload

    @staticmethod
    def _required_string(values: Mapping[str, Any], name: str) -> str:
        value = values.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"repo action requires non-empty '{name}'")
        return value.strip()

    @staticmethod
    def _optional_string(values: Mapping[str, Any], name: str) -> str | None:
        value = values.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _require_write_access(repo_id: str, entry: Mapping[str, Any]) -> None:
        if entry.get("access") != "write":
            raise ValueError(f"repo '{repo_id}' has read-only access")

    def _repo_path(self, entry: Mapping[str, Any]) -> Path:
        raw = self._required_string(entry, "local_path")
        path = Path(raw)
        return path if path.is_absolute() else self._managed_root() / path

    def _worktree_path(self, repo_id: str, branch: str) -> Path:
        slug = _BRANCH_SLUG_RE.sub("-", branch).strip(".-") or "branch"
        return self._managed_root() / f"{repo_id}.worktrees" / slug

    def _managed_root(self) -> Path:
        return self._managed_root_override or get_config().storage.worktree_dir

    def _remote_url(self, entry: Mapping[str, Any]) -> str:
        remote = self._optional_string(entry, "remote_url")
        if remote is None:
            raise ValueError("repo has no remote_url to clone")
        if (
            remote.startswith(("file://", "http://", "https://", "git@", "ssh://"))
            or Path(remote).is_absolute()
        ):
            return remote
        if remote.startswith("github.com/"):
            return f"https://{remote.removesuffix('.git')}.git"
        return remote

    async def _is_git_checkout(self, path: Path) -> bool:
        try:
            return (await self._git_stdout(path, "rev-parse", "--is-inside-work-tree")) == "true"
        except ValueError:
            return False

    async def _require_checkout(self, repo_id: str, path: Path) -> None:
        if not path.is_dir() or not await self._is_git_checkout(path):
            raise ValueError(f"repo '{repo_id}' checkout is missing or not a git checkout: {path}")

    async def _configure_identity(self, path: Path) -> None:
        await self._git(
            path, "config", "user.name", os.getenv("PROGENITOR_GIT_AUTHOR_NAME", "Progenitor")
        )
        await self._git(
            path,
            "config",
            "user.email",
            os.getenv("PROGENITOR_GIT_AUTHOR_EMAIL", "progenitor@users.noreply.github.com"),
        )

    async def _default_branch(self, path: Path) -> str:
        remote_head = await self._git_stdout_optional(
            path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"
        )
        if remote_head and remote_head.startswith("origin/"):
            return remote_head.removeprefix("origin/")
        branches = await self._git_stdout(
            path, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/"
        )
        choices = [
            line.removeprefix("origin/")
            for line in branches.splitlines()
            if line and not line.endswith("/HEAD")
        ]
        for preferred in ("main", "master"):
            if preferred in choices:
                return preferred
        if choices:
            return choices[0]
        raise ValueError(f"could not determine origin default branch for {path}")

    async def _listed_worktrees(self, checkout: Path) -> list[tuple[Path, str]]:
        output = await self._git_stdout(checkout, "worktree", "list", "--porcelain")
        worktrees: list[tuple[Path, str]] = []
        path: Path | None = None
        for line in [*output.splitlines(), ""]:
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line.startswith("branch refs/heads/") and path is not None:
                worktrees.append((path, line.removeprefix("branch refs/heads/")))
            elif not line:
                path = None
        return worktrees

    async def _git_stdout(self, cwd: Path, *args: str) -> str:
        stdout, _ = await self._git(cwd, *args)
        return stdout

    async def _git_stdout_optional(self, cwd: Path, *args: str) -> str | None:
        try:
            return await self._git_stdout(cwd, *args)
        except ValueError:
            return None

    async def _ref_exists(self, cwd: Path, ref: str) -> bool:
        return (
            await self._git_stdout_optional(cwd, "rev-parse", "--verify", "--quiet", ref)
        ) is not None

    async def _git(self, cwd: Path, *args: str, use_credentials: bool = False) -> tuple[str, str]:
        command = ["git"]
        if use_credentials and os.environ.get("GH_TOKEN"):
            command.extend(
                [
                    "-c",
                    "credential.helper=",
                    "-c",
                    'credential.helper=!f() { test -n "$GH_TOKEN" && printf \'username=x-access-token\\npassword=%s\\n\' "$GH_TOKEN"; }; f',
                ]
            )
        command.extend(args)
        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": self._git_env(),
        }
        uid = os.getenv("PROGENITOR_REPO_UID")
        gid = os.getenv("PROGENITOR_REPO_GID")
        if uid:
            kwargs["user"] = int(uid)
        if gid:
            kwargs["group"] = int(gid)
        try:
            proc = await asyncio.create_subprocess_exec(*command, **kwargs)
            stdout_raw, stderr_raw = await proc.communicate()
        except OSError as exc:
            raise ValueError(f"git command could not start: {self._redact(str(exc))}") from exc
        stdout = stdout_raw.decode("utf-8", "replace").strip()
        stderr = stderr_raw.decode("utf-8", "replace").strip()
        if proc.returncode:
            detail = self._redact(stderr or stdout or f"exit code {proc.returncode}")
            raise ValueError(f"git {' '.join(args[:2])} failed: {detail}")
        return stdout, stderr

    @staticmethod
    def _git_env() -> dict[str, str]:
        env = dict(os.environ)
        if os.getenv("PROGENITOR_REPO_UID"):
            # The backend's own HOME (root's) is unreadable to the repo UID, and
            # git fails on an unreadable global config.
            env["HOME"] = tempfile.gettempdir()
        return env

    @staticmethod
    def _github_repo(remote: str) -> tuple[str | None, str | None]:
        normalized = remote.removesuffix(".git")
        if normalized.startswith("git@github.com:"):
            path = normalized.split(":", 1)[1]
        else:
            parsed = urlparse(normalized)
            if parsed.hostname != "github.com":
                return None, None
            path = parsed.path.lstrip("/")
        parts = path.split("/")
        return (parts[0], parts[1]) if len(parts) == 2 and all(parts) else (None, None)

    def _raise_api_error(self, response: httpx.Response, token: str) -> None:
        if response.is_error:
            body = self._redact(response.text, token)
            raise ValueError(f"GitHub API {response.status_code}: {body[:500]}")

    @staticmethod
    def _redact(text: str, token: str | None = None) -> str:
        return redact_secrets(text.replace(token, "[REDACTED]") if token else text)
