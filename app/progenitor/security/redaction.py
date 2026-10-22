"""Security domain contracts."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# Patterns for secret redaction - (compiled_pattern, replacement)
# Order matters: more specific patterns should come first
SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-proj-[A-Za-z0-9\-_]{20,}"), "[OPENAI_PROJECT_KEY_REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[OPENAI_KEY_REDACTED]"),
    (
        re.compile(
            r'(?i)(?:azure|az)[_-]?(?:openai)?[_-]?(?:api)?[_-]?key["\']?\s*[:=]\s*["\']?([A-Za-z0-9]{32,})'
        ),
        "[AZURE_KEY_REDACTED]",
    ),
    (re.compile(r"(?i)(AZURE_[A-Z_]*KEY)\s*=\s*([A-Za-z0-9]{32,})"), r"\1=[AZURE_KEY_REDACTED]"),
    (
        re.compile(
            r'(?i)(?:key|secret|credential|token)["\']?\s*[:=]\s*["\']?([A-Fa-f0-9]{32})(?![A-Za-z0-9])'
        ),
        "[AZURE_KEY_REDACTED]",
    ),
    (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "[GITHUB_TOKEN_REDACTED]"),
    (re.compile(r"gho_[A-Za-z0-9]{36,}"), "[GITHUB_OAUTH_REDACTED]"),
    (re.compile(r"ghs_[A-Za-z0-9]{36,}"), "[GITHUB_SERVER_REDACTED]"),
    (re.compile(r"ghu_[A-Za-z0-9]{36,}"), "[GITHUB_USER_REDACTED]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{22,}"), "[GITHUB_PAT_REDACTED]"),
    (re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"), "[ANTHROPIC_KEY_REDACTED]"),
    (
        re.compile(r'(?i)(api[_-]?key|apikey)["\']?\s*[:=]\s*["\']?([^"\',\s]{8,})'),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r'(?i)(secret|password|passwd|pwd|credential)["\']?\s*[:=]\s*["\']?([^"\',\s]{8,})'
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"), r"\1[REDACTED]"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "[AWS_ACCESS_KEY_REDACTED]"),
    (
        re.compile(
            r'(?i)(aws[_-]?secret[_-]?access[_-]?key)["\']?\s*[:=]\s*["\']?[A-Za-z0-9/+=]{40}'
        ),
        r"\1=[REDACTED]",
    ),
]


def redact_secrets(text: str) -> str:
    """Redact sensitive information from text before logging."""
    result = text
    for pattern, replacement in SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result
