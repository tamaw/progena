"""Policies capabilities."""

from __future__ import annotations

from typing import Any

from deepagents.backends.protocol import FileInfo, LsResult, WriteResult

from app.progenitor.agent.filesystem.routines import RoutineDirectoryBackend


class PolicyDirectoryBackend(RoutineDirectoryBackend):
    """Validated policy JSON files backed by the shared Postgres document store."""

    _ASYNC_ONLY = "PolicyDirectoryBackend is async-only; use the async file tools."

    @staticmethod
    def _name(file_path: str) -> str:
        return file_path.lstrip("/").removeprefix("policies/")

    async def _resolve_routine(self, name: str) -> tuple[str, dict[str, Any]] | None:
        policy_id = self._parse_routine_name(name)
        if policy_id is None:
            return None
        document = await self._sm.get_execution_policy(policy_id)
        return (policy_id, document) if document is not None else None

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        from app.progenitor.settings.policies import parse_policy_document

        policy_id = self._parse_routine_name(self._name(file_path))
        if policy_id is None:
            return WriteResult(error="Expected /policies/<id>.json")
        try:
            document = parse_policy_document(content)
            if document["id"] != policy_id:
                raise ValueError("Policy id must match filename")
            await self._sm.save_execution_policy(policy_id, document)
        except ValueError as exc:
            return WriteResult(error=str(exc))
        return WriteResult(path=file_path)

    async def adelete(self, file_path: str) -> WriteResult:
        resolved = await self._resolve_routine(self._name(file_path))
        if resolved is None:
            return WriteResult(error=f"File '{file_path}' not found")
        await self._sm.delete_execution_policy(resolved[0])
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        documents = await self._sm.list_execution_policies()
        return LsResult(
            entries=[
                FileInfo(path=f"/{doc['id']}.json", is_dir=False)
                for doc in sorted(documents, key=lambda doc: doc["id"])
            ]
        )
