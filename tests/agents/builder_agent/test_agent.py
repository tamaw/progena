from app.agents.builder_agent.agent import _safe_log_text


def test_safe_log_text_redacts_github_pat_in_clone_url():
    clone_url = (
        "https://x-access-token:"
        "github_pat_1234567890abcdefghijklmnopqrstuv"
        "@github.com/tamaw/progenitor.git"
    )

    redacted = _safe_log_text(clone_url)

    assert "github_pat_" not in redacted
    assert "[GITHUB_PAT_REDACTED]" in redacted
