from pathlib import Path

import pytest

from devbrief.domain.contracts import Plan
from devbrief.integration.repository import (
    RepositoryEvidenceError,
    WorkspaceRepositoryEvidence,
)
from devbrief.integration.task_system import TaskSystemDraftClient


def test_repository_evidence_is_bounded_and_redacted(tmp_path: Path) -> None:
    file = tmp_path / "config.txt"
    file.write_text("token=secret-value\nuseful decision", encoding="utf-8")
    result = WorkspaceRepositoryEvidence(tmp_path).read("config.txt")
    assert result.reference == "repo://config.txt"
    assert "secret-value" not in result.summary
    assert "[REDACTED]" in result.summary


def test_repository_evidence_rejects_escape_and_oversize(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_bytes(b"x" * 20)
    reader = WorkspaceRepositoryEvidence(tmp_path, max_bytes=10)
    with pytest.raises(RepositoryEvidenceError):
        reader.read("../outside.txt")
    with pytest.raises(RepositoryEvidenceError):
        reader.read("large.txt")


def test_task_system_draft_defaults_to_dry_run() -> None:
    plan = Plan(title="t", body="b", repository="o/r", tool_name="create_issue")
    record = TaskSystemDraftClient().create_draft(plan)
    assert record.dry_run is True
    assert record.draft_id.startswith("draft-")
