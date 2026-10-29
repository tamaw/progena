"""Regression coverage for shared virtual-backend search behavior."""

from __future__ import annotations

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import FileInfo, LsResult, ReadResult
from deepagents.backends.state import StateBackend
from deepagents.backends.utils import create_file_data

from src.progena.agent.filesystem.base import _AsyncOnlyFileBackendMixin


class _TrackingVirtualBackend(_AsyncOnlyFileBackendMixin):
    """Small virtual tree that makes accidental hydration fail loudly."""

    def __init__(self, files: dict[str, str | None]) -> None:
        self._files = files
        self.listed_paths: list[str] = []
        self.read_paths: list[str] = []

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        self.read_paths.append(file_path)
        content = self._files[file_path]
        if content is None:
            raise AssertionError(f"excluded file was hydrated: {file_path}")
        return ReadResult(file_data=create_file_data(content))

    async def als(self, path: str) -> LsResult:
        self.listed_paths.append(path)
        root = path.rstrip("/") or "/"
        prefix = "/" if root == "/" else f"{root}/"
        files: list[FileInfo] = []
        directories: set[str] = set()
        for file_path in self._files:
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            name, separator, _tail = remainder.partition("/")
            if separator:
                directories.add(f"{prefix}{name}")
            else:
                files.append(FileInfo(path=file_path, is_dir=False))
        entries = [FileInfo(path=directory, is_dir=True) for directory in sorted(directories)]
        entries.extend(sorted(files, key=lambda entry: entry["path"]))
        return LsResult(entries=entries)


async def test_grep_applies_date_glob_before_hydrating_mounted_virtual_files():
    runs = _TrackingVirtualBackend(
        {
            "/2026-10-29_alpha.md": "needle for the selected date\n",
            "/2026-10-30_beta.md": None,
            "/archive/2026-10-29_old.md": None,
        }
    )
    backend = CompositeBackend(default=StateBackend(), routes={"/runs/": runs})

    result = await backend.agrep("needle", path="/runs/", glob="/2026-10-29_*.md")

    assert result.matches == [
        {
            "path": "/runs/2026-10-29_alpha.md",
            "line": 1,
            "text": "needle for the selected date",
        }
    ]
    assert runs.read_paths == ["/2026-10-29_alpha.md"]


async def test_grep_scopes_virtual_tree_traversal_to_requested_subtree():
    backend = _TrackingVirtualBackend(
        {
            "/days/2026-10-29/selected.md": "needle in selected subtree\n",
            "/days/2026-10-30/excluded.md": None,
            "/unrelated/excluded.md": None,
        }
    )

    result = await backend.agrep("needle", path="/days/2026-10-29")

    assert [match["path"] for match in result.matches or []] == ["/days/2026-10-29/selected.md"]
    assert "/unrelated" not in backend.listed_paths
    assert "/days/2026-10-30" not in backend.listed_paths
    assert backend.read_paths == ["/days/2026-10-29/selected.md"]


async def test_grep_uses_sdk_nested_brace_and_exact_file_glob_semantics_before_reading():
    backend = _TrackingVirtualBackend(
        {
            "/nested/report.md": "needle in markdown\n",
            "/nested/report.json": "needle in json\n",
            "/nested/deep/report.md": "needle in nested markdown\n",
            "/nested/ignored.txt": None,
            "/outside.md": None,
        }
    )

    recursive = await backend.agrep("needle", path="/", glob="nested/deep/**/*.md")
    assert [match["path"] for match in recursive.matches or []] == ["/nested/deep/report.md"]
    assert backend.read_paths == ["/nested/deep/report.md"]

    backend.read_paths.clear()
    brace = await backend.agrep("needle", path="/", glob="nested/*.{md,json}")
    assert [match["path"] for match in brace.matches or []] == [
        "/nested/report.json",
        "/nested/report.md",
    ]
    assert backend.read_paths == [
        "/nested/report.json",
        "/nested/report.md",
    ]

    backend.read_paths.clear()
    basename = await backend.agrep("needle", path="/", glob="report.md")
    assert [match["path"] for match in basename.matches or []] == [
        "/nested/report.md",
        "/nested/deep/report.md",
    ]
    assert backend.read_paths == [
        "/nested/report.md",
        "/nested/deep/report.md",
    ]

    backend.read_paths.clear()
    exact = await backend.agrep("needle", path="/nested/report.md", glob="*.md")
    assert [match["path"] for match in exact.matches or []] == ["/nested/report.md"]
    assert backend.read_paths == ["/nested/report.md"]


async def test_grep_stops_after_a_second_match_in_the_first_file_at_max_count():
    first = "/dated/00-first.md"
    backend = _TrackingVirtualBackend(
        {
            first: "needle first\nneedle second\n",
            **{f"/dated/{index:02}-later.md": "needle later\n" for index in range(1, 40)},
        }
    )

    result = await backend.agrep("needle", path="/", glob="dated/*.md", max_count=1)

    assert result.matches == [{"path": first, "line": 1, "text": "needle first"}]
    assert result.truncated is True
    assert backend.read_paths == [first]
