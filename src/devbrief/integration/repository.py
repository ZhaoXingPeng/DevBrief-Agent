from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from devbrief.domain.trace import redact_summary


class RepositoryEvidenceError(ValueError):
    """A safe, user-facing repository evidence validation error."""


@dataclass(frozen=True, slots=True)
class RepositoryEvidence:
    reference: str
    path: str
    digest: str
    summary: str
    bytes_read: int


class WorkspaceRepositoryEvidence:
    """Read bounded, redacted evidence from files inside an explicit workspace."""

    def __init__(self, workspace: Path, *, max_bytes: int = 128 * 1024) -> None:
        self.workspace = workspace.resolve()
        self.max_bytes = max_bytes
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

    def read(self, relative_path: str, *, max_lines: int = 80) -> RepositoryEvidence:
        if not relative_path or Path(relative_path).is_absolute():
            raise RepositoryEvidenceError("repository path must be relative")
        candidate = (self.workspace / relative_path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise RepositoryEvidenceError("repository path must remain in workspace")
        if not candidate.is_file():
            raise RepositoryEvidenceError("repository file does not exist")
        size = candidate.stat().st_size
        if size > self.max_bytes:
            raise RepositoryEvidenceError("repository file exceeds evidence size limit")
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RepositoryEvidenceError(
                "repository file is not readable text"
            ) from exc
        lines = text.splitlines()[: max(1, max_lines)]
        summary = redact_summary(_redact_text("\n".join(lines)), limit=2000)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        relative = candidate.relative_to(self.workspace).as_posix()
        return RepositoryEvidence(
            reference=f"repo://{relative}",
            path=relative,
            digest=digest,
            summary=summary,
            bytes_read=len(text.encode("utf-8")),
        )


def _redact_text(value: str) -> str:
    value = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", value
    )
    value = re.sub(
        r"\b(?:gh[pousr]_\w{8,}|github_pat_\w{8,}|sk-[A-Za-z0-9_-]{8,})\b",
        "[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)((?:token|password|secret)\s*[=:：]\s*)[^\s,;]+", r"\1[REDACTED]", value
    )
    return value
