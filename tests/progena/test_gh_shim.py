"""Offline tests for the gh shim against a fake downstream gh (never the real one)."""

import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GH_SHIM = ROOT / "data" / "shims" / "bin" / "gh"
FAKE_STATUS = 37
PR_URL = "https://github.com/org/repo/pull/12"

FAKE_GH = """#!/bin/bash
printf '%s\\0' "$@" > "$GH_FAKE_LOG"
exit "$GH_FAKE_STATUS"
"""

PR_COMMENT = ("pr", "comment", "12", "--body", "hello")
PR_REVIEW = ("pr", "review", "12", "--comment", "--body", "hello")
PR_CREATE = ("pr", "create", "--title", "x", "--body", "y")

NOT_ONE = ["", "0", "true", "yes", "1 ", " 1", "11"]
REFUSED_OPT_INS = [
    (None, None),
    ("1", None),
    (None, "1"),
    *(("1", value) for value in NOT_ONE),
    *((value, "1") for value in NOT_ONE),
]


class FakeGh:
    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "fake-bin"
        self.bin.mkdir()
        executable = self.bin / "gh"
        executable.write_text(FAKE_GH, encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        self.log = tmp_path / "argv.bin"
        self.home = tmp_path

    def run(
        self,
        *args: str,
        write: str | None = None,
        publish: str | None = None,
        extra: dict[str, str] | None = None,
        status: int = FAKE_STATUS,
    ) -> subprocess.CompletedProcess[str]:
        # Minimal, credential-free environment; the shim only reaches the fake.
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.home),
            "PROGENA_SHIM_SYSTEM_PATH": str(self.bin),
            "GH_FAKE_LOG": str(self.log),
            "GH_FAKE_STATUS": str(status),
        }
        if write is not None:
            environment["PROGENA_SHIM_GIT_WRITE"] = write
        if publish is not None:
            environment["PROGENA_SHIM_GIT_PUBLISH"] = publish
        environment.update(extra or {})
        return subprocess.run(
            [str(GH_SHIM), *args],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def argv(self) -> list[str] | None:
        if not self.log.exists():
            return None
        return self.log.read_bytes().decode("utf-8").split("\0")[:-1]


@pytest.fixture
def fake_gh(tmp_path: Path) -> FakeGh:
    return FakeGh(tmp_path)


def _assert_forwarded(
    fake_gh: FakeGh, result: subprocess.CompletedProcess[str], arguments: tuple[str, ...]
) -> None:
    assert result.returncode == FAKE_STATUS, result.stderr
    assert fake_gh.argv() == list(arguments)


def _assert_refused(fake_gh: FakeGh, result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 2
    assert "progena-shim: refused:" in result.stderr
    assert fake_gh.argv() is None


def test_shim_reaches_only_the_fake_gh(fake_gh: FakeGh) -> None:
    result = fake_gh.run("auth", "status")

    _assert_forwarded(fake_gh, result, ("auth", "status"))
    assert sorted(path.name for path in fake_gh.bin.iterdir()) == ["gh"]


@pytest.mark.parametrize("arguments", [PR_COMMENT, PR_REVIEW, PR_CREATE])
@pytest.mark.parametrize(("write", "publish"), REFUSED_OPT_INS)
def test_publishing_commands_need_both_opt_ins_exactly_one(
    fake_gh: FakeGh, arguments: tuple[str, ...], write: str | None, publish: str | None
) -> None:
    _assert_refused(fake_gh, fake_gh.run(*arguments, write=write, publish=publish))


@pytest.mark.parametrize("arguments", [PR_COMMENT, PR_REVIEW, PR_CREATE])
def test_publishing_commands_delegate_with_both_opt_ins(
    fake_gh: FakeGh, arguments: tuple[str, ...]
) -> None:
    _assert_forwarded(fake_gh, fake_gh.run(*arguments, write="1", publish="1"), arguments)


@pytest.mark.parametrize("arguments", [PR_COMMENT, PR_REVIEW])
def test_legacy_or_alias_opt_in_names_do_not_grant(
    fake_gh: FakeGh, arguments: tuple[str, ...]
) -> None:
    legacy = {
        "PROGENITOR_SHIM_GIT_WRITE": "1",
        "PROGENITOR_SHIM_GIT_PUBLISH": "1",
        "SHIM_GIT_WRITE": "1",
        "SHIM_GIT_PUBLISH": "1",
    }
    _assert_refused(fake_gh, fake_gh.run(*arguments, extra=legacy))


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "comment", "12", "--body", "hello world"),
        ("pr", "comment", PR_URL, "--body-file", "notes.md"),
        ("pr", "comment", "12", "-b", "--approve"),
        ("pr", "comment", "12", "-F", "-"),
        ("pr", "review", "12", "--comment", "--body", "looks good"),
        ("pr", "review", PR_URL, "-c", "-b", "looks good"),
        ("pr", "review", "12", "--comment", "--body-file", "review.md"),
        ("pr", "review", "12", "-c", "-F", "review.md"),
        ("pr", "review", "--comment", "--body=text", "12"),
        ("pr", "review", "12", "--body-file=review.md", "--comment"),
        ("pr", "review", "12", "--body", "first", "-c"),
        ("pr", "review", "12", "--comment", "--body", "--approve"),
        ("pr", "review", "12", "--comment", "-b", "-r please"),
        ("pr", "review", "12", "--comment", "--body", "--comment=x -a"),
        ("pr", "review", "--comment", "--body", ""),
        ("pr", "review", "--comment", "--body", "x", "--", "12"),
        ("pr", "review", "12", "--comment", "--comment", "--body", "x"),
    ],
)
def test_comment_operations_forward_argv_unchanged(
    fake_gh: FakeGh, arguments: tuple[str, ...]
) -> None:
    _assert_forwarded(fake_gh, fake_gh.run(*arguments, write="1", publish="1"), arguments)


@pytest.mark.parametrize("status", [0, 1, 4])
def test_downstream_exit_status_is_forwarded(fake_gh: FakeGh, status: int) -> None:
    result = fake_gh.run(*PR_REVIEW, write="1", publish="1", status=status)

    assert result.returncode == status
    assert fake_gh.argv() == list(PR_REVIEW)


@pytest.mark.parametrize(
    "arguments",
    [
        # No comment mode.
        ("pr", "review", "12"),
        ("pr", "review", "12", "--body", "text"),
        # Approving or requesting changes, alone or mixed in either order.
        ("pr", "review", "12", "--approve"),
        ("pr", "review", "12", "-a"),
        ("pr", "review", "12", "--request-changes", "--body", "x"),
        ("pr", "review", "12", "-r", "-b", "x"),
        ("pr", "review", "12", "--comment", "--approve"),
        ("pr", "review", "12", "--approve", "--comment"),
        ("pr", "review", "12", "-c", "-a"),
        ("pr", "review", "12", "-a", "-c"),
        ("pr", "review", "12", "--comment", "--request-changes", "-b", "x"),
        ("pr", "review", "12", "-r", "-c", "-b", "x"),
        # Mode equals forms and short-flag clusters.
        ("pr", "review", "12", "--comment=true"),
        ("pr", "review", "12", "--comment=false", "-c"),
        ("pr", "review", "12", "-c", "--approve=true"),
        ("pr", "review", "12", "-c", "--request-changes=1"),
        ("pr", "review", "12", "-c=1"),
        ("pr", "review", "12", "-ca"),
        ("pr", "review", "12", "-ac"),
        ("pr", "review", "12", "-cb", "text"),
        ("pr", "review", "12", "-c", "-btext"),
        ("pr", "review", "12", "-c", "-b=text"),
        # Missing option values.
        ("pr", "review", "12", "--comment", "--body"),
        ("pr", "review", "12", "-c", "-b"),
        ("pr", "review", "12", "-c", "--body-file"),
        ("pr", "review", "12", "-c", "-F"),
        # Unknown or unsupported syntax.
        ("pr", "review", "12", "-c", "--repo", "org/repo"),
        ("pr", "review", "12", "-c", "-R", "org/repo"),
        ("pr", "review", "12", "-c", "--web"),
        ("pr", "review", "12", "-c", "-"),
        ("pr", "review", "12", "--comments"),
        ("pr", "review", "1", "2", "--comment"),
        ("pr", "review", "--comment", "--", "1", "2"),
        # Comment tokens that are only option values or follow --.
        ("pr", "review", "12", "--body", "--comment"),
        ("pr", "review", "12", "-b", "-c"),
        ("pr", "review", "12", "--body-file", "--comment"),
        ("pr", "review", "12", "-F", "-c"),
        ("pr", "review", "12", "--body=--comment"),
        ("pr", "review", "12", "--body-file=-c"),
        ("pr", "review", "12", "--", "--comment"),
        ("pr", "review", "--", "-c"),
        ("pr", "review", "12", "--approve", "--body", "--comment"),
    ],
)
def test_non_comment_reviews_are_refused(fake_gh: FakeGh, arguments: tuple[str, ...]) -> None:
    _assert_refused(fake_gh, fake_gh.run(*arguments, write="1", publish="1"))


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "review", "12", "--approve"),
        ("pr", "review", "12", "--comment", "--approve"),
        ("pr", "review", "12", "--body", "--comment"),
    ],
)
@pytest.mark.parametrize(("write", "publish"), [(None, None), ("1", None), (None, "1")])
def test_non_comment_reviews_are_refused_without_opt_ins(
    fake_gh: FakeGh, arguments: tuple[str, ...], write: str | None, publish: str | None
) -> None:
    _assert_refused(fake_gh, fake_gh.run(*arguments, write=write, publish=publish))


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "merge", "12"),
        ("pr", "merge", "12", "--comment"),
        ("pr", "edit", "12", "--body", "x"),
        ("pr", "close", "12", "--comment", "x"),
        ("pr", "ready", "12"),
        ("issue", "comment", "12", "--body", "x"),
        ("api", "/user"),
        ("api", "repos/org/repo/pulls/12/comments", "-f", "body=x"),
        ("auth", "token"),
        ("repo", "delete", "org/repo"),
        ("release", "create", "v1"),
    ],
)
def test_other_commands_stay_refused_with_both_opt_ins(
    fake_gh: FakeGh, arguments: tuple[str, ...]
) -> None:
    _assert_refused(fake_gh, fake_gh.run(*arguments, write="1", publish="1"))


@pytest.mark.parametrize("base", [PR_COMMENT, PR_REVIEW])
@pytest.mark.parametrize(
    "flags",
    [
        ("-X", "POST"),
        ("--method", "POST"),
        ("--method=POST",),
        ("--hostname", "evil.example"),
        ("--hostname=evil.example",),
        ("--template", "{{.}}"),
        ("--template={{.}}",),
        # Global refusals hold even when the token is body text.
        ("--body", "-X"),
        ("--body", "--hostname=evil.example"),
    ],
)
def test_unsafe_options_stay_refused_on_comment_operations(
    fake_gh: FakeGh, base: tuple[str, ...], flags: tuple[str, ...]
) -> None:
    _assert_refused(fake_gh, fake_gh.run(*base, *flags, write="1", publish="1"))


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "view", "12"),
        ("pr", "list", "--head", "branch", "--state", "open"),
        ("pr", "checks", "12"),
        ("repo", "view"),
        ("auth", "status"),
    ],
)
def test_read_only_commands_still_forward_without_opt_ins(
    fake_gh: FakeGh, arguments: tuple[str, ...]
) -> None:
    _assert_forwarded(fake_gh, fake_gh.run(*arguments), arguments)
