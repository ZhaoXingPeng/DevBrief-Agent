from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from devbrief import cli
from devbrief.application.metrics import RuntimeMetricsService
from devbrief.application.orchestration import BugTriageApplication
from devbrief.domain.contracts import (
    Checkpoint,
    ExecutionBudget,
    RuntimeMetricsSnapshot,
    SessionState,
    TraceKind,
    TraceSpan,
    TriageRunResult,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.integration.storage import SQLiteRunStore

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def _budget() -> ExecutionBudget:
    return ExecutionBudget(
        max_steps=16,
        max_tool_calls=4,
        deadline_at=NOW + timedelta(minutes=5),
        max_model_tokens=1000,
        max_cost=1.0,
        consumed_steps=4,
        consumed_tool_calls=1,
        consumed_model_tokens=37,
        consumed_cost=0.25,
    )


def _result_and_artifacts() -> tuple[
    TriageRunResult, tuple[TraceSpan, ...], tuple[Checkpoint, ...]
]:
    application = BugTriageApplication(now=lambda: NOW)
    result = application.run(
        Path("fixtures/transcripts/bug-triage-redacted-v1.json"), budget=_budget()
    )
    return (
        result,
        application.harness.traces.list_for(result.trace_id),
        application.harness.checkpoints.list_for(result.session_id),
    )


def test_runtime_metrics_aggregates_redacted_sqlite_records(tmp_path: Path) -> None:
    result, traces, checkpoints = _result_and_artifacts()
    database = tmp_path / "runs.sqlite3"
    SQLiteRunStore(database).save(result, traces=traces, checkpoints=checkpoints)

    snapshot = RuntimeMetricsService().collect(database)

    assert snapshot.schema_version == 1
    assert snapshot.session_count == 1
    assert snapshot.state_counts == {SessionState.AWAITING_APPROVAL: 1}
    assert snapshot.trace_kind_counts.get(TraceKind.MODEL_CALL, 0) == 0
    assert snapshot.trace_kind_counts[TraceKind.STATE_TRANSITION] == 5
    assert snapshot.tool_counts == {}
    assert snapshot.span_count == len(traces)
    assert snapshot.model_call_count == 0
    assert snapshot.tool_call_count == 0
    assert snapshot.checkpoint_count == len(checkpoints)
    assert snapshot.recover_count == 0
    assert snapshot.trace_integrity_counts == {"verified": 1}
    assert snapshot.trace_integrity_mismatch_counts == {}
    assert snapshot.budget_totals.consumed_steps == 8
    assert snapshot.budget_totals.consumed_model_tokens == 37
    serialized = snapshot.model_dump_json()
    assert "Synthetic authentication refresh" not in serialized
    assert "state changed" not in serialized
    assert "fixture=bug-triage" not in serialized


def test_runtime_metrics_counts_error_tool_model_and_recovery_events(
    tmp_path: Path,
) -> None:
    result, traces, checkpoints = _result_and_artifacts()
    failed = result.model_copy(
        update={
            "session_id": "ses_failed",
            "trace_id": "trc_failed",
            "state": SessionState.FAILED_RECOVERABLE,
            "error": ErrorCode.DEADLINE_EXCEEDED.value,
            "budget": _budget(),
        }
    )
    failed_traces = (
        TraceSpan(
            span_id="spn_failed_1",
            trace_id="trc_failed",
            session_id="ses_failed",
            kind=TraceKind.STATE_TRANSITION,
            state_before=SessionState.EXECUTING,
            state_after=SessionState.FAILED_RECOVERABLE,
            error_code=ErrorCode.DEADLINE_EXCEEDED.value,
        ),
        TraceSpan(
            span_id="spn_failed_2",
            trace_id="trc_failed",
            session_id="ses_failed",
            kind=TraceKind.MODEL_CALL,
        ),
        TraceSpan(
            span_id="spn_failed_3",
            trace_id="trc_failed",
            session_id="ses_failed",
            kind=TraceKind.TOOL_CALL,
            tool_name="create_issue",
            error_code=ErrorCode.UNKNOWN_OUTCOME.value,
        ),
        TraceSpan(
            span_id="spn_failed_4",
            trace_id="trc_failed",
            session_id="ses_failed",
            kind=TraceKind.STATE_TRANSITION,
            state_before=SessionState.FAILED_RECOVERABLE,
            state_after=SessionState.EXECUTING,
        ),
    )
    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    store.save(result, traces=traces, checkpoints=checkpoints)
    store.save(failed, traces=failed_traces)

    snapshot = RuntimeMetricsService().collect(database)

    assert snapshot.session_count == 2
    assert snapshot.state_counts[SessionState.FAILED_RECOVERABLE] == 1
    assert snapshot.error_counts == {
        ErrorCode.DEADLINE_EXCEEDED: 1,
        ErrorCode.UNKNOWN_OUTCOME: 1,
    }
    assert snapshot.trace_kind_counts[TraceKind.MODEL_CALL] == 1
    assert snapshot.trace_kind_counts[TraceKind.TOOL_CALL] == 1
    assert snapshot.tool_counts == {"create_issue": 1}
    assert snapshot.span_count == len(traces) + len(failed_traces)
    assert snapshot.model_call_count == 1
    assert snapshot.tool_call_count == 1
    assert snapshot.recover_count == 1
    assert snapshot.budget_totals.consumed_steps == 12
    assert snapshot.budget_totals.consumed_tool_calls == 2


def test_runtime_metrics_rejects_corrupt_artifact_without_raw_data(
    tmp_path: Path,
) -> None:
    result, traces, checkpoints = _result_and_artifacts()
    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    store.save(result, traces=traces, checkpoints=checkpoints)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE triage_runs SET trace_json = ? WHERE session_id = ?",
            (json.dumps({"private": "token=secret"}), result.session_id),
        )

    with pytest.raises(DevBriefError) as raised:
        RuntimeMetricsService().collect(database)

    assert raised.value.code is ErrorCode.VALIDATION_ERROR
    assert "token=secret" not in str(raised.value)


def test_metrics_contract_rejects_unknown_schema_and_negative_counts() -> None:
    with pytest.raises(ValidationError):
        RuntimeMetricsSnapshot.model_validate(
            {"schema_version": 99, "session_count": 0}
        )

    with pytest.raises(ValidationError):
        RuntimeMetricsSnapshot.model_validate(
            {
                "session_count": -1,
                "span_count": 0,
                "model_call_count": 0,
                "tool_call_count": 0,
                "checkpoint_count": 0,
                "recover_count": 0,
                "budget_totals": {
                    "session_count": -1,
                    "consumed_steps": 0,
                    "consumed_tool_calls": 0,
                    "consumed_model_tokens": 0,
                    "consumed_cost": 0,
                },
            }
        )


def test_runtime_metrics_reports_invalid_trace_without_exposing_trace_content(
    tmp_path: Path,
) -> None:
    result, traces, checkpoints = _result_and_artifacts()
    database = tmp_path / "runs.sqlite3"
    SQLiteRunStore(database).save(result, traces=traces, checkpoints=checkpoints)
    tampered = list(traces)
    tampered[0] = tampered[0].model_copy(
        update={"output_summary": "Authorization: Bearer private-token"}
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE triage_runs SET trace_json = ? WHERE session_id = ?",
            (
                json.dumps([item.model_dump(mode="json") for item in tampered]),
                result.session_id,
            ),
        )

    snapshot = RuntimeMetricsService().collect(database)

    assert snapshot.trace_integrity_counts == {"invalid": 1}
    assert snapshot.trace_integrity_mismatch_counts == {
        "checkpoint_trace_hash_mismatch": 1,
        "trace_integrity_hash_mismatch": 1,
        "trace_integrity_previous_hash_mismatch": 1,
    }
    assert "private-token" not in snapshot.model_dump_json()


def test_cli_metrics_missing_database_does_not_create_or_emit_partial_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "missing.sqlite3"
    output = tmp_path / "metrics.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["devbrief", "metrics", "--db", str(database), "--output", str(output)],
    )

    assert cli.main() == 2
    assert not database.exists()
    assert not output.exists()
    assert "devbrief error:" in capsys.readouterr().err


def test_cli_metrics_writes_only_a_redacted_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result, traces, checkpoints = _result_and_artifacts()
    database = tmp_path / "runs.sqlite3"
    output = tmp_path / "metrics.json"
    SQLiteRunStore(database).save(result, traces=traces, checkpoints=checkpoints)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devbrief", "metrics", "--db", str(database), "--output", str(output)],
    )

    assert cli.main() == 0
    assert capsys.readouterr().out == ""
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["session_count"] == 1
    assert "Synthetic authentication refresh" not in output.read_text(encoding="utf-8")
    assert "state changed" not in output.read_text(encoding="utf-8")
