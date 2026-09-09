from __future__ import annotations

from devbrief.domain.contracts import SessionState, TraceKind, TraceSpan
from devbrief.domain.replay import TraceReplayer


def span(
    span_id: str,
    *,
    trace_id: str = "trc_1",
    session_id: str = "ses_1",
    before: SessionState | None = None,
    after: SessionState | None = None,
    plan_hash: str | None = None,
    tool_name: str | None = None,
    tool_decision: str | None = None,
    receipt_id: str | None = None,
) -> TraceSpan:
    return TraceSpan(
        span_id=span_id,
        trace_id=trace_id,
        session_id=session_id,
        kind=TraceKind.STATE_TRANSITION if after is not None else TraceKind.TOOL_CALL,
        state_before=before,
        state_after=after,
        plan_hash=plan_hash,
        tool_name=tool_name,
        tool_decision=tool_decision,
        receipt_id=receipt_id,
        input_summary="private transcript and token=secret",
    )


def test_replayer_rebuilds_state_and_extracts_execution_metadata() -> None:
    spans = [
        span("spn_1", after=SessionState.CREATED),
        span("spn_2", before=SessionState.CREATED, after=SessionState.PLANNING),
        span(
            "spn_3",
            plan_hash="sha256:abc",
            tool_name="create_issue",
            tool_decision="allowed",
            receipt_id="rcpt_1",
        ),
    ]

    report = TraceReplayer().replay(
        spans,
        expected_final_state=SessionState.PLANNING,
        expected_plan_hash="sha256:abc",
        expected_tool_decisions=["create_issue:allowed"],
        expected_receipt_refs=["rcpt_1"],
    )

    assert report.replayable is True
    assert report.trace_id == "trc_1"
    assert report.session_id == "ses_1"
    assert report.state_sequence == [SessionState.CREATED, SessionState.PLANNING]
    assert report.final_state is SessionState.PLANNING
    assert report.plan_hashes == ["sha256:abc"]
    assert report.tool_decisions == ["create_issue:allowed"]
    assert report.receipt_refs == ["rcpt_1"]
    assert report.mismatches == []
    assert "private transcript" not in report.model_dump_json()
    assert "secret" not in report.model_dump_json()


def test_policy_and_approval_spans_expose_structured_replay_fields() -> None:
    item = span(
        "spn_policy",
        plan_hash="sha256:abc",
        tool_name="create_issue",
        tool_decision="denied",
        receipt_id="rcpt_1",
    )

    report = TraceReplayer().replay(
        [span("spn_state", after=SessionState.CREATED), item],
        expected_final_state=SessionState.CREATED,
        expected_plan_hash="sha256:abc",
        expected_tool_decisions=["create_issue:denied"],
        expected_receipt_refs=["rcpt_1"],
    )

    assert report.replayable is True


def test_empty_duplicate_and_cross_session_traces_report_stable_mismatches() -> None:
    replayer = TraceReplayer()

    empty = replayer.replay([])
    assert empty.replayable is False
    assert empty.mismatches == ["trace_empty"]

    duplicate = replayer.replay(
        [
            span("spn_1", after=SessionState.CREATED),
            span("spn_1", before=SessionState.CREATED, after=SessionState.PLANNING),
        ]
    )
    assert duplicate.replayable is False
    assert "duplicate_span_id" in duplicate.mismatches

    cross_session = replayer.replay(
        [
            span("spn_a", after=SessionState.CREATED),
            span(
                "spn_b",
                trace_id="trc_other",
                before=SessionState.CREATED,
                after=SessionState.PLANNING,
            ),
        ]
    )
    assert cross_session.replayable is False
    assert "trace_id_mismatch" in cross_session.mismatches


def test_replayer_reports_state_and_expected_metadata_drift_without_values() -> None:
    report = TraceReplayer().replay(
        [
            span("spn_1", after=SessionState.CREATED),
            span("spn_2", before=SessionState.ANALYZING, after=SessionState.PLANNING),
        ],
        expected_final_state=SessionState.COMPLETED,
        expected_plan_hash="sha256:expected",
        expected_tool_decisions=["read:allowed"],
        expected_receipt_refs=["rcpt_expected"],
    )

    assert report.replayable is False
    assert "state_before_mismatch" in report.mismatches
    assert "final_state_mismatch" in report.mismatches
    assert "plan_hash_mismatch" in report.mismatches
    assert "tool_decisions_mismatch" in report.mismatches
    assert "receipt_refs_mismatch" in report.mismatches
    assert "expected" not in report.model_dump_json()
