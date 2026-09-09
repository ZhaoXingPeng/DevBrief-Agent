from __future__ import annotations

import re

_SECRET_PATTERNS = (
    (
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,})\b"),
        "[REDACTED]",
    ),
    (re.compile(r"(?i)((?:token|password|secret)\s*=\s*)[^\s,;]+"), r"\1[REDACTED]"),
)


def redact_summary(value: str, *, limit: int = 240) -> str:
    """Redact common credential forms and cap retained trace text."""
    redacted = value
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted[:limit]
