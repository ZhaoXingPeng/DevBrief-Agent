from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from devbrief.domain.contracts import (
    ApprovalStatus,
    ExecutionBudget,
    Plan,
    SessionState,
    TaskDraft,
    TriageRunResult,
)
from devbrief.integration.github import GitHubError, GitHubIssueClient
from devbrief.integration.media import segments_from_response
from devbrief.integration.storage import SQLiteRunStore


def result() -> TriageRunResult:
    plan = Plan(
        title="Fix auth refresh",
        body="Evidence-backed task",
        repository="org/repo",
        tool_name="create_issue",
        arguments={"title": "Fix auth refresh", "body": "Evidence-backed task"},
    )
    draft = TaskDraft(
        plan=plan,
        plan_hash="sha256:" + "a" * 64,
        evidence_refs=["fixture://bug#seg-1"],
    )
    return TriageRunResult(
        session_id="ses_test",
        trace_id="trc_ses_test",
        state=SessionState.AWAITING_APPROVAL,
        draft=draft,
        approval_status=ApprovalStatus.PENDING,
        budget=ExecutionBudget(
            max_steps=8,
            max_tool_calls=2,
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            max_model_tokens=100,
            max_cost=1.0,
        ),
    )


def test_sqlite_store_round_trips_redacted_result(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "devbrief.sqlite3")
    expected = result()

    store.save(expected)

    assert store.get(expected.session_id) == expected
    assert store.list_metadata()[0]["state"] == "awaiting_approval"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM triage_runs").fetchone()[0] == 1


def test_github_client_defaults_to_safe_dry_run() -> None:
    issue = GitHubIssueClient().create_issue(
        repository="org/repo", title="A task", body="Details"
    )

    assert issue.dry_run is True
    assert issue.number is None


def test_github_client_requires_token_for_live_write() -> None:
    client = GitHubIssueClient(token=None, dry_run=False)

    try:
        client.create_issue(repository="org/repo", title="A task", body="Details")
    except GitHubError as exc:
        assert "TOKEN" in str(exc)
    else:
        raise AssertionError("live GitHub write must require a token")


def test_asr_response_becomes_ordered_redacted_segments() -> None:
    segments = segments_from_response(
        {
            "segments": [
                {"text": "token=private-value", "start": 0.0, "end": 1.25},
                {"text": "next decision", "start": 1.25, "end": 2.0},
            ]
        },
        "unused",
    )

    assert segments[0].text == "token=[REDACTED]"
    assert segments[0].end_ms == 1250
    assert segments[1].start_ms == 1250
