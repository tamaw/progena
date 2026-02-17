from app.progenitor.tools.gh import _extract_pr_url


def test_extract_pr_url():
    out = "https://github.com/octo-org/octo-repo/pull/123\n"
    assert _extract_pr_url(out) == "https://github.com/octo-org/octo-repo/pull/123"


def test_extract_pr_url_none():
    assert _extract_pr_url("nope") is None

