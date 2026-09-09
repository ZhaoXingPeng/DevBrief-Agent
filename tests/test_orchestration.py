from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from devbrief.application.analyzer import FakeBugTriageAnalyzer
from devbrief.application.orchestration import BugTriageApplication
from devbrief.domain.contracts import ApprovalStatus, ExecutionBudget, SessionState
from devbrief.domain.errors import DevBriefError, ErrorCode

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def fixture_payload(text: str) -> dict[str, object]:
    return {
        "fixture_id": "bug-triage-redacted-v1",
        "fixture_version": "1.0.0",
        "redacted": True,
        "segments": [
            {
                "segment_id": "seg_001",
                "start_ms": 0,
                "end_ms": 2400,
                "speaker": "engineer_a",
                "text": text,
            },
            {
                "segment_id": "seg_002",
                "start_ms": 2600,
                "end_ms": 5100,
                "speaker": "engineer_b",
                "text": "The owner and due date remain unknown until confirmation.",
            },
        ],
    }


def write_fixture(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture_payload(text)), encoding="utf-8")
    return path


def budget(**overrides: object) -> ExecutionBudget:
    return ExecutionBudget(
        max_steps=overrides.get("max_steps", 8),  # type: ignore[arg-type]
        max_tool_calls=2,
        deadline_at=overrides.get("deadline_at", NOW + timedelta(minutes=5)),  # type: ignore[arg-type]
        max_model_tokens=100,
        max_cost=2.0,
    )


def test_application_runs_to_approval_boundary_without_executing_write(
    tmp_path: Path,
) -> None:
    private_text = (
        "token=private-value; Synthetic authentication refresh failure "
        "needs a tracked fix."
    )
    application = BugTriageApplication(now=lambda: NOW)

    result = application.run(write_fixture(tmp_path, private_text), budget=budget())

    assert result.state is SessionState.AWAITING_APPROVAL
    assert result.approval_status is ApprovalStatus.PENDING
    assert result.candidates
    assert result.draft.plan_hash.startswith("sha256:")
    assert result.draft.evidence_refs == [
        "fixture://bug-triage-redacted-v1/1.0.0#seg_001"
    ]
    assert result.budget.consumed_steps == 4
    assert result.budget.consumed_tool_calls == 0
    trace = application.harness.traces.list_for(result.trace_id)
    states = [span.state_after for span in trace if span.state_after is not None]
    assert states == [
        SessionState.CREATED,
        SessionState.INGESTING,
        SessionState.ANALYZING,
        SessionState.PLANNING,
        SessionState.AWAITING_APPROVAL,
    ]
    serialized = result.model_dump_json()
    assert private_text not in serialized
    assert "private-value" not in serialized


def test_application_is_deterministic_for_same_fixture_and_budget(
    tmp_path: Path,
) -> None:
    path = write_fixture(
        tmp_path, "Synthetic authentication refresh failure needs a tracked fix."
    )

    first = BugTriageApplication(now=lambda: NOW).run(path, budget=budget())
    second = BugTriageApplication(now=lambda: NOW).run(path, budget=budget())

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_application_stops_after_analyzer_schema_failure_and_keeps_checkpoint(
    tmp_path: Path,
) -> None:
    analyzer = FakeBugTriageAnalyzer(output_builder=lambda _: [{"kind": "invalid"}])
    application = BugTriageApplication(
        now=lambda: NOW,
        analyzer=analyzer,
    )

    with pytest.raises(DevBriefError) as raised:
        application.run(
            write_fixture(tmp_path, "A failure needs triage."), budget=budget()
        )

    assert raised.value.code is ErrorCode.VALIDATION_ERROR
    session = application.harness.get_session("ses_a8b0b6db06274fa0")
    assert session.state is SessionState.FAILED_TERMINAL
    checkpoint = application.harness.checkpoints.load_latest(session.session_id)
    assert checkpoint is not None
    assert checkpoint.state is SessionState.ANALYZING


def test_application_does_not_start_session_for_invalid_fixture(tmp_path: Path) -> None:
    path = write_fixture(
        tmp_path, "Synthetic authentication refresh failure needs a tracked fix."
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["redacted"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    application = BugTriageApplication(now=lambda: NOW)

    with pytest.raises(DevBriefError) as raised:
        application.run(path, budget=budget())

    assert raised.value.code is ErrorCode.VALIDATION_ERROR
    assert application.harness.traces.list_for("trc_ses_a8b0b6db06274fa0") == ()


def test_application_budget_failure_is_not_allowed_to_reach_approval(
    tmp_path: Path,
) -> None:
    application = BugTriageApplication(now=lambda: NOW)

    with pytest.raises(DevBriefError) as raised:
        application.run(
            write_fixture(
                tmp_path,
                "Synthetic authentication refresh failure needs a tracked fix.",
            ),
            budget=budget(max_steps=2),
        )

    assert raised.value.code is ErrorCode.BUDGET_EXHAUSTED
    session = application.harness.get_session("ses_a8b0b6db06274fa0")
    assert session.state is SessionState.FAILED_TERMINAL
