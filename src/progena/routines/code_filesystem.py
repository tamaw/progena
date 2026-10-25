"""Scoped asynchronous filesystem tools for QuickJS routine code nodes.

This module deliberately depends only on the backend protocol's duck-typed async
surface.  Runtime assembly owns which backend instance a routine receives.
"""

from __future__ import annotations

import asyncio
import posixpath
import threading
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future
from typing import Any, TypeVar

from deepagents.middleware.filesystem import _resolve_real_path


class FilesystemToolError(ValueError):
    """A filesystem operation rejected by the routine code capability."""


T = TypeVar("T")


class CallerLoopBridge:
    """Run backend coroutines on the graph's caller loop from a QuickJS worker.

    QuickJS hosts async callbacks on its own driving loop.  Postgres-backed
    filesystem adapters are loop-scoped, so callbacks must schedule their real
    backend coroutine on the graph loop instead.  Cancellation waits for that
    coroutine's ``finally`` cleanup before allowing the host callback to end.
    """

    def __init__(self, caller_loop: asyncio.AbstractEventLoop) -> None:
        self._caller_loop = caller_loop
        self._cancelled = False
        self._lock = threading.Lock()
        self._calls: set[Future[Any]] = set()

    def cancel(self) -> None:
        """Cancel every backend call currently owned by this code execution."""
        with self._lock:
            self._cancelled = True
            calls = tuple(self._calls)
        for call in calls:
            call.cancel()

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Await ``operation`` on the caller loop and return its result."""
        with self._lock:
            if self._cancelled:
                raise asyncio.CancelledError

        complete = threading.Event()
        started = threading.Event()

        async def run_operation() -> T:
            started.set()
            try:
                return await operation()
            finally:
                complete.set()

        coroutine = run_operation()
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._caller_loop)
        except BaseException:
            coroutine.close()
            raise

        def complete_if_never_started(_: Future[Any]) -> None:
            if not started.is_set():
                complete.set()

        future.add_done_callback(complete_if_never_started)

        with self._lock:
            if self._cancelled:
                future.cancel()
                cancelled_before_wait = True
            else:
                self._calls.add(future)
                cancelled_before_wait = False

        if cancelled_before_wait:
            await asyncio.shield(asyncio.to_thread(complete.wait))
            raise asyncio.CancelledError

        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            # ``Future.cancel`` acknowledges delivery, not completion of the
            # caller-loop coroutine.  Wait for its cleanup so no database/file
            # mutation remains active after the QuickJS run has been cancelled.
            await asyncio.shield(asyncio.to_thread(complete.wait))
            raise
        finally:
            with self._lock:
                self._calls.discard(future)


def _as_mapping(value: Any, method: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FilesystemToolError(f"{method}: arguments must be an object")
    return value


def _required_string(args: Mapping[str, Any], name: str, method: str) -> str:
    value = args.get(name)
    if not isinstance(value, str):
        raise FilesystemToolError(f"{method}: '{name}' must be a string")
    return value


def _optional_integer(args: Mapping[str, Any], name: str, method: str) -> int | None:
    value = args.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise FilesystemToolError(f"{method}: '{name}' must be an integer")
    return value


def _canonical_path(value: str, *, label: str) -> str:
    """Canonicalize an absolute virtual path without allowing escapes."""
    if not value:
        raise FilesystemToolError(f"{label} must not be empty")
    if "\x00" in value:
        raise FilesystemToolError(f"{label} must not contain a null byte")
    if "\\" in value:
        raise FilesystemToolError(f"{label} must use forward slashes")
    if not value.startswith("/"):
        raise FilesystemToolError(f"{label} must be absolute")

    parts: list[str] = []
    for part in value.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise FilesystemToolError(f"{label} must not contain path traversal")
        parts.append(part)
    return "/" + "/".join(parts)


def _canonical_scope(value: str) -> str:
    if any(part in {".", ".."} for part in value.split("/")):
        raise FilesystemToolError("filesystem scope must not contain dot segments")
    return _canonical_path(value, label="filesystem scope")


def _canonical_pattern(value: str, *, label: str) -> str:
    """Reject glob/include-glob attempts to escape the selected search root."""
    if not value:
        raise FilesystemToolError(f"{label} must not be empty")
    if "\x00" in value:
        raise FilesystemToolError(f"{label} must not contain a null byte")
    if "\\" in value:
        raise FilesystemToolError(f"{label} must use forward slashes")
    if value.startswith("/"):
        raise FilesystemToolError(f"{label} must be relative to the scoped path")
    if ".." in value.split("/"):
        raise FilesystemToolError(f"{label} must not contain path traversal")
    return value


def _is_missing_path_error(error: Any) -> bool:
    """Backends report absence as prose ("not found") or a code ("path_not_found")."""
    text = str(error).lower()
    return "not found" in text or "not_found" in text


def _result_error(operation: str, result: Any) -> None:
    error = getattr(result, "error", None)
    if error:
        raise FilesystemToolError(f"{operation}: {error}")


def _file_data_from_read_result(operation: str, result: Any) -> Mapping[str, Any]:
    _result_error(operation, result)
    file_data = getattr(result, "file_data", None)
    if not isinstance(file_data, Mapping):
        raise FilesystemToolError(f"{operation}: backend returned no file content")
    return file_data


def _text_from_read_result(operation: str, result: Any) -> str:
    file_data = _file_data_from_read_result(operation, result)
    encoding = file_data.get("encoding")
    if encoding not in (None, "utf-8"):
        raise FilesystemToolError(f"{operation}: binary files are not supported")
    content = file_data.get("content")
    if not isinstance(content, str):
        raise FilesystemToolError(f"{operation}: backend returned non-text content")
    return content


class ScopedFilesystemTools:
    """The code-node filesystem capability, constrained to configured mounts."""

    def __init__(self, backend: Any, filesystem: Any) -> None:
        if backend is None:
            raise ValueError(
                "code node filesystem is enabled but no filesystem backend was supplied"
            )
        if hasattr(filesystem, "model_dump"):
            filesystem = filesystem.model_dump(mode="python")
        if not isinstance(filesystem, Mapping):
            raise ValueError("code node filesystem configuration must be an object")

        access = filesystem.get("access")
        if access not in {"read", "read_write"}:
            raise ValueError("code node filesystem access must be 'read' or 'read_write'")
        raw_paths = filesystem.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ValueError("code node filesystem paths must be a non-empty list")

        scopes: list[str] = []
        for raw_path in raw_paths:
            if not isinstance(raw_path, str):
                raise ValueError("code node filesystem paths must contain strings")
            scope = _canonical_scope(raw_path)
            if scope == "/":
                raise ValueError("code node filesystem scopes must not be empty")
            if not (scope == "/artifacts" or scope.startswith("/artifacts/")) and not (
                scope == "/shared" or scope.startswith("/shared/")
            ):
                raise ValueError(
                    "code node filesystem scopes must be under /artifacts/ or /shared/"
                )
            if scope not in scopes:
                scopes.append(scope)

        routes = getattr(backend, "routes", None)
        if isinstance(routes, Mapping) and hasattr(backend, "default"):
            for scope in scopes:
                if self._explicit_route_backend(backend, scope) is None:
                    raise ValueError(
                        f"code node filesystem scope is not mounted by the backend: {scope}/"
                    )

        self._access = access
        self._backend = backend
        self._scopes = tuple(scopes)

    @property
    def writable(self) -> bool:
        return self._access == "read_write"

    def _path(self, value: str, *, label: str) -> str:
        path = _canonical_path(value, label=label)
        if not any(path == scope or path.startswith(f"{scope}/") for scope in self._scopes):
            raise FilesystemToolError(f"{label} is outside the configured filesystem scopes")
        # Match SDK permissions: symlink targets must also remain in scope.
        try:
            resolved = _resolve_real_path(self._backend, path)
        except (ValueError, OSError, RuntimeError) as exc:
            raise FilesystemToolError(f"{label} cannot be resolved safely") from exc
        if not any(resolved == scope or resolved.startswith(f"{scope}/") for scope in self._scopes):
            raise FilesystemToolError(f"{label} resolves outside the configured filesystem scopes")
        return path

    def _optional_path(self, args: Mapping[str, Any], name: str, method: str) -> str | None:
        value = args.get(name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise FilesystemToolError(f"{method}: '{name}' must be a string")
        return self._path(value, label=f"{method} {name}")

    def _require_write(self, method: str) -> None:
        if not self.writable:
            raise FilesystemToolError(f"{method}: filesystem access is read-only")

    @staticmethod
    def _explicit_route_backend(backend: Any, path: str) -> Any | None:
        routes = getattr(backend, "routes", None)
        if not isinstance(routes, Mapping) or not hasattr(backend, "default"):
            return None
        for prefix, mounted in sorted(
            routes.items(), key=lambda item: len(str(item[0])), reverse=True
        ):
            normalized_prefix = str(prefix).rstrip("/")
            if path == normalized_prefix or path.startswith(f"{normalized_prefix}/"):
                return mounted
        return None

    def _backend_mount(self, path: str) -> int:
        """Identify the routed backend object selected for ``path``.

        ``CompositeBackend`` chooses the longest route prefix.  Duck-typing its
        public ``routes``/``default`` attributes avoids coupling this capability
        to runtime filesystem assembly while still rejecting cross-mount moves.
        """
        mounted = self._explicit_route_backend(self._backend, path)
        if mounted is not None:
            return id(mounted)
        routes = getattr(self._backend, "routes", None)
        if isinstance(routes, Mapping) and hasattr(self._backend, "default"):
            return id(self._backend.default)
        return id(self._backend)

    async def ls(self, value: Any) -> list[dict[str, Any]]:
        args = _as_mapping(value, "ls")
        path = self._path(_required_string(args, "path", "ls"), label="ls path")
        result = await self._backend.als(path)
        _result_error("ls", result)
        entries = getattr(result, "entries", None) or []
        safe_entries: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            entry_path = entry.get("path")
            if not isinstance(entry_path, str):
                continue
            try:
                canonical_entry_path = self._path(entry_path, label="ls result path")
            except FilesystemToolError:
                continue
            safe_entries.append({**entry, "path": canonical_entry_path})
        return safe_entries

    async def read_file(self, value: Any) -> str:
        args = _as_mapping(value, "readFile")
        path = self._path(
            _required_string(args, "file_path", "readFile"), label="readFile file_path"
        )
        offset = _optional_integer(args, "offset", "readFile")
        limit = _optional_integer(args, "limit", "readFile")
        result = await self._backend.aread(
            path,
            0 if offset is None else offset,
            2_000 if limit is None else limit,
        )
        return _text_from_read_result("readFile", result)

    async def glob(self, value: Any) -> dict[str, Any]:
        args = _as_mapping(value, "glob")
        pattern = _canonical_pattern(
            _required_string(args, "pattern", "glob"), label="glob pattern"
        )
        requested_path = self._optional_path(args, "path", "glob")
        paths = (requested_path,) if requested_path is not None else self._scopes
        matches: list[str] = []
        truncated = False
        truncation_reason: str | None = None
        for path in paths:
            result = await self._backend.aglob(pattern, path)
            _result_error("glob", result)
            truncated = truncated or bool(getattr(result, "truncated", False))
            if truncation_reason is None:
                truncation_reason = getattr(result, "truncation_reason", None)
            for match in getattr(result, "matches", None) or []:
                if not isinstance(match, Mapping):
                    continue
                matched_path = match.get("path")
                if not isinstance(matched_path, str):
                    continue
                try:
                    canonical_match = self._path(matched_path, label="glob result path")
                except FilesystemToolError:
                    continue
                if canonical_match not in matches:
                    matches.append(canonical_match)
        return {
            "matches": matches,
            "truncated": truncated,
            "truncation_reason": truncation_reason,
        }

    async def grep(self, value: Any) -> dict[str, Any]:
        args = _as_mapping(value, "grep")
        pattern = _required_string(args, "pattern", "grep")
        if "\x00" in pattern:
            raise FilesystemToolError("grep pattern must not contain a null byte")
        search_glob = args.get("glob")
        if search_glob is not None:
            if not isinstance(search_glob, str):
                raise FilesystemToolError("grep: 'glob' must be a string")
            search_glob = _canonical_pattern(search_glob, label="grep glob")
        requested_path = self._optional_path(args, "path", "grep")
        max_count = _optional_integer(args, "max_count", "grep")
        if max_count is not None and max_count <= 0:
            raise FilesystemToolError("grep: 'max_count' must be positive")

        paths = (requested_path,) if requested_path is not None else self._scopes
        matches: list[dict[str, Any]] = []
        truncated = False
        for path in paths:
            remaining = None if max_count is None else max_count - len(matches)
            if remaining == 0:
                truncated = True
                break
            result = await self._backend.agrep(
                pattern,
                path,
                search_glob,
                max_count=remaining,
            )
            _result_error("grep", result)
            truncated = truncated or bool(getattr(result, "truncated", False))
            for match in getattr(result, "matches", None) or []:
                if not isinstance(match, Mapping):
                    continue
                match_path = match.get("path")
                if not isinstance(match_path, str):
                    continue
                try:
                    canonical_match_path = self._path(match_path, label="grep result path")
                except FilesystemToolError:
                    continue
                matches.append({**match, "path": canonical_match_path})
        return {"matches": matches, "truncated": truncated}

    async def write_file(self, value: Any) -> dict[str, str]:
        self._require_write("writeFile")
        args = _as_mapping(value, "writeFile")
        path = self._path(
            _required_string(args, "file_path", "writeFile"), label="writeFile file_path"
        )
        content = _required_string(args, "content", "writeFile")
        await self._require_missing_destination(path, operation="writeFile")
        result = await self._backend.awrite(path, content)
        _result_error("writeFile", result)
        return {"path": getattr(result, "path", None) or path}

    async def edit_file(self, value: Any) -> dict[str, Any]:
        self._require_write("editFile")
        args = _as_mapping(value, "editFile")
        path = self._path(
            _required_string(args, "file_path", "editFile"), label="editFile file_path"
        )
        old_string = _required_string(args, "old_string", "editFile")
        new_string = _required_string(args, "new_string", "editFile")
        replace_all = args.get("replace_all", False)
        if not isinstance(replace_all, bool):
            raise FilesystemToolError("editFile: 'replace_all' must be a boolean")
        result = await self._backend.aedit(path, old_string, new_string, replace_all)
        _result_error("editFile", result)
        return {
            "path": getattr(result, "path", None) or path,
            "occurrences": getattr(result, "occurrences", None),
        }

    async def _file_text_for_copy(self, path: str) -> str:
        # Check the source through the normal text read API first.  It provides
        # clear source errors and rejects directories/base64 content before a
        # copy can call any mutation method.
        first_page = await self._backend.aread(path, 0, 1)
        _text_from_read_result("copyFile source", first_page)

        try:
            downloads = await self._backend.adownload_files([path])
        except (AttributeError, NotImplementedError) as exc:
            raise FilesystemToolError(
                "copyFile source: backend cannot provide exact file content"
            ) from exc
        if not downloads:
            raise FilesystemToolError("copyFile source: backend returned no download content")
        download = downloads[0]
        error = getattr(download, "error", None)
        content = getattr(download, "content", None)
        if error or content is None:
            raise FilesystemToolError(f"copyFile source: {error or 'backend returned no content'}")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FilesystemToolError("copyFile source: binary files are not supported") from exc

    async def _require_missing_destination(self, path: str, *, operation: str) -> None:
        read_result = await self._backend.aread(path, 0, 1)
        if getattr(read_result, "error", None) is None:
            raise FilesystemToolError(f"{operation}: destination already exists: {path}")

        parent = posixpath.dirname(path) or "/"
        listing = await self._backend.als(parent)
        listing_error = getattr(listing, "error", None)
        if listing_error and not _is_missing_path_error(listing_error):
            raise FilesystemToolError(
                f"{operation}: unable to inspect destination: {listing_error}"
            )
        if not listing_error:
            for entry in getattr(listing, "entries", None) or []:
                if (
                    isinstance(entry, Mapping)
                    and isinstance(entry.get("path"), str)
                    and entry["path"].rstrip("/") == path
                ):
                    raise FilesystemToolError(f"{operation}: destination already exists: {path}")

        error = getattr(read_result, "error", None)
        if not isinstance(error, str) or not _is_missing_path_error(error):
            raise FilesystemToolError(
                f"{operation}: unable to establish destination absence: "
                f"{error or 'unknown read error'}"
            )

    async def _copy(self, source_path: str, destination_path: str, *, operation: str) -> None:
        content = await self._file_text_for_copy(source_path)
        await self._require_missing_destination(destination_path, operation=operation)
        result = await self._backend.awrite(destination_path, content)
        _result_error(operation, result)

    async def copy_file(self, value: Any) -> dict[str, str]:
        self._require_write("copyFile")
        args = _as_mapping(value, "copyFile")
        source_path = self._path(
            _required_string(args, "source_path", "copyFile"), label="copyFile source_path"
        )
        destination_path = self._path(
            _required_string(args, "destination_path", "copyFile"),
            label="copyFile destination_path",
        )
        await self._copy(source_path, destination_path, operation="copyFile")
        return {"path": destination_path}

    async def _delete_file(self, path: str, *, operation: str) -> dict[str, str]:
        source = await self._backend.aread(path, 0, 1)
        _file_data_from_read_result(f"{operation} source", source)
        result = await self._backend.adelete(path)
        _result_error(operation, result)
        return {"path": getattr(result, "path", None) or path}

    async def delete_file(self, value: Any) -> dict[str, str]:
        self._require_write("deleteFile")
        args = _as_mapping(value, "deleteFile")
        path = self._path(
            _required_string(args, "file_path", "deleteFile"), label="deleteFile file_path"
        )
        return await self._delete_file(path, operation="deleteFile")

    async def _rollback_destination(self, destination_path: str) -> str | None:
        try:
            result = await self._backend.adelete(destination_path)
            error = getattr(result, "error", None)
            return str(error) if error else None
        except Exception as exc:  # noqa: BLE001 - preserve the original move failure below
            return str(exc)

    async def move_file(self, value: Any) -> dict[str, str]:
        """Move one text file inside a single mount.

        Backends do not currently standardize a move operation.  The fallback is
        deliberately non-atomic: copy without overwrite, then delete source.
        If deleting the source fails, it removes the newly-created destination
        best-effort and surfaces the original failure instead of claiming a move.
        """
        self._require_write("moveFile")
        args = _as_mapping(value, "moveFile")
        source_path = self._path(
            _required_string(args, "source_path", "moveFile"), label="moveFile source_path"
        )
        destination_path = self._path(
            _required_string(args, "destination_path", "moveFile"),
            label="moveFile destination_path",
        )
        if self._backend_mount(source_path) != self._backend_mount(destination_path):
            raise FilesystemToolError("moveFile: cross-mount moves are not supported")

        source = await self._backend.aread(source_path, 0, 1)
        _text_from_read_result("moveFile source", source)

        await self._require_missing_destination(destination_path, operation="moveFile")

        await self._copy(source_path, destination_path, operation="moveFile")
        try:
            await self._delete_file(source_path, operation="moveFile")
        except Exception as exc:
            rollback_error = await self._rollback_destination(destination_path)
            rollback_note = (
                "destination rollback failed: " + rollback_error
                if rollback_error
                else "destination rolled back"
            )
            raise FilesystemToolError(
                f"moveFile: source deletion failed after non-atomic copy ({rollback_note}): {exc}"
            ) from exc
        return {"path": destination_path}
