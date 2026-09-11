from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from devbrief.domain.contracts import (
    ApprovalStatus,
    ExecutionBudget,
    Plan,
    SessionState,
    TaskDraft,
    ToolReceiptStatus,
    ToolRequest,
    TriageRunResult,
)
from devbrief.integration import github as github_module
from devbrief.integration.github import (
    GitHubError,
    GitHubIssueClient,
    GitHubIssueProvider,
    idempotency_marker,
)
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


def test_github_client_enforces_configured_repository_scope() -> None:
    client = GitHubIssueClient(allowed_repositories={"owner/allowed"})
    try:
        client.create_issue(repository="owner/other", title="A", body="B")
    except GitHubError as exc:
        assert "scope" in str(exc)
    else:
        raise AssertionError("repository scope must be enforced")


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _github_request() -> ToolRequest:
    return ToolRequest(
        tool_call_id="call_github",
        tool_name="create_issue",
        arguments={
            "repository": "owner/repo",
            "title": "Task",
            "body": "Details",
        },
        session_id="ses_github",
        trace_id="trc_github",
        idempotency_key="idem_github_1",
    )


def test_github_provider_binds_marker_to_create_and_queries_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[object] = []

    def fake_urlopen(request: object, *, timeout: float) -> _Response:
        del timeout
        requests.append(request)
        if getattr(request, "method", None) == "GET":
            if sum(getattr(item, "method", None) == "GET" for item in requests) < 3:
                return _Response({"items": []})
            return _Response(
                {
                    "items": [
                        {
                            "number": 42,
                            "html_url": "https://github.com/owner/repo/issues/42",
                            "title": "Task",
                            "body": f"Details\n\n{idempotency_marker('idem_github_1')}",
                        }
                    ]
                }
            )
        return _Response(
            {
                "number": 42,
                "html_url": "https://github.com/owner/repo/issues/42",
                "title": "Task",
            }
        )

    monkeypatch.setattr(github_module, "urlopen", fake_urlopen)
    client = GitHubIssueClient(
        token="test-token",
        dry_run=False,
        allowed_repositories={"owner/repo"},
    )
    provider = GitHubIssueProvider(client)
    request = _github_request()

    created = provider.create(request)
    assert created.status is ToolReceiptStatus.SUCCEEDED
    assert created.external_object_id == "42"
    post = next(item for item in requests if getattr(item, "method", None) == "POST")
    post_data = getattr(post, "data", None)
    assert isinstance(post_data, bytes)
    assert (
        idempotency_marker("idem_github_1")
        in json.loads(post_data.decode("utf-8"))["body"]
    )

    recovered = provider.query(request)
    assert recovered is not None
    assert recovered.external_object_id == "42"
    assert getattr(requests[-1], "method", None) == "GET"
    assert "search/issues?" in requests[-1].full_url  # type: ignore[union-attr]


def test_github_create_reuses_existing_issue_before_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[object] = []

    def fake_urlopen(request: object, *, timeout: float) -> _Response:
        del timeout
        requests.append(request)
        if getattr(request, "method", None) == "GET":
            return _Response(
                {
                    "items": [
                        {
                            "number": 7,
                            "html_url": "https://github.com/owner/repo/issues/7",
                            "body": idempotency_marker("idem_github_1"),
                        }
                    ]
                }
            )
        raise AssertionError("existing idempotency marker must prevent POST")

    monkeypatch.setattr(github_module, "urlopen", fake_urlopen)
    client = GitHubIssueClient(
        token="test-token",
        dry_run=False,
        allowed_repositories={"owner/repo"},
    )

    issue = client.create_issue(
        repository="owner/repo",
        title="Task",
        body="Details",
        idempotency_key="idem_github_1",
    )

    assert issue.number == 7
    assert issue.dry_run is False
    assert all(getattr(item, "method", None) == "GET" for item in requests)


def test_github_query_rejects_multiple_marker_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: object, *, timeout: float) -> _Response:
        del request, timeout
        return _Response(
            {
                "items": [
                    {
                        "number": 1,
                        "html_url": "https://github.com/owner/repo/issues/1",
                        "body": idempotency_marker("idem_github_1"),
                    },
                    {
                        "number": 2,
                        "html_url": "https://github.com/owner/repo/issues/2",
                        "body": idempotency_marker("idem_github_1"),
                    },
                ]
            }
        )

    monkeypatch.setattr(
        github_module,
        "urlopen",
        fake_urlopen,
    )
    client = GitHubIssueClient(
        token="test-token",
        dry_run=False,
        allowed_repositories={"owner/repo"},
    )

    with pytest.raises(GitHubError, match="multiple"):
        client.find_issue_by_idempotency(
            repository="owner/repo", idempotency_key="idem_github_1"
        )


def test_github_query_falls_back_to_digest_search_when_comment_is_not_indexed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[object] = []

    def fake_urlopen(request: object, *, timeout: float) -> _Response:
        del timeout
        requests.append(request)
        if len(requests) == 1:
            return _Response({"items": []})
        return _Response(
            {
                "items": [
                    {
                        "number": 42,
                        "html_url": "https://github.com/owner/repo/issues/42",
                        "body": idempotency_marker("idem_github_1"),
                    }
                ]
            }
        )

    monkeypatch.setattr(github_module, "urlopen", fake_urlopen)
    client = GitHubIssueClient(
        token="test-token",
        dry_run=False,
        allowed_repositories={"owner/repo"},
    )

    issue = client.find_issue_by_idempotency(
        repository="owner/repo", idempotency_key="idem_github_1"
    )

    assert issue is not None
    assert issue.number == 42
    assert len(requests) == 2
    assert "search/issues?" in requests[1].full_url  # type: ignore[union-attr]
    assert "devbrief-idempotency%3A" not in requests[1].full_url  # type: ignore[union-attr]


def test_github_provider_turns_transport_timeout_into_unknown_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout_urlopen(request: object, *, timeout: float) -> _Response:
        del request, timeout
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(github_module, "urlopen", timeout_urlopen)
    provider = GitHubIssueProvider(
        GitHubIssueClient(
            token="test-token",
            dry_run=False,
            allowed_repositories={"owner/repo"},
        )
    )

    receipt = provider.create(_github_request())

    assert receipt.status is ToolReceiptStatus.UNKNOWN_OUTCOME
    assert receipt.external_object_id is None
    assert receipt.idempotency_key == "idem_github_1"


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
