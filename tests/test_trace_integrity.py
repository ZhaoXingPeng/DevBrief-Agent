from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from devbrief.application.harness import Harness
from devbrief.domain.contracts import (
    ExecutionBudget,
    SessionState,
    TraceIntegrityStatus,
    TraceKind,
    TraceSpan,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.replay import TraceReplayer

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def _budget() -> ExecutionBudget:
    return ExecutionBudget(
        max_steps=8,
        max_tool_calls=2,
        deadline_at=NOW + timedelta(minutes=5),
        max_model_tokens=100,
        max_cost=2.0,
    )


def test_replay_rejects_a_tampered_span_against_checkpoint_anchor() -> None:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session("ses_trace_integrity", _budget())
    harness.transition(session.session_id, SessionState.INGESTING)

    spans = list(harness.traces.list_for(session.trace_id))
    spans[1] = spans[1].model_copy(update={"output_summary": "altered"})

    report = TraceReplayer().replay(
        spans,
        checkpoints=harness.checkpoints.list_for(session.session_id),
    )

    assert report.replayable is False
    assert report.integrity_status is TraceIntegrityStatus.INVALID
    assert "trace_integrity_hash_mismatch" in report.mismatches


def test_harness_hash_links_spans_and_checkpoints_without_retaining_secret() -> None:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session("ses_trace_chain", _budget())
    harness.record_ingest(
        session.session_id,
        fixture_id="redacted-fixture",
        version="1.0.0",
        segments=1,
    )
    harness.transition(session.session_id, SessionState.INGESTING)

    spans = harness.traces.list_for(session.trace_id)
    checkpoints = harness.checkpoints.list_for(session.session_id)

    assert [span.sequence for span in spans] == list(range(1, len(spans) + 1))
    assert all(span.integrity_hash is not None for span in spans)
    assert spans[0].previous_hash is None
    assert all(
        checkpoint.trace_span_count is not None
        and checkpoint.trace_head_hash is not None
        and checkpoint.checkpoint_integrity_hash is not None
        for checkpoint in checkpoints
    )
    report = TraceReplayer().replay(spans, checkpoints=checkpoints)
    assert report.replayable is True
    assert report.integrity_status is TraceIntegrityStatus.VERIFIED
    assert report.integrity_mismatches == []
    assert "redacted-fixture" not in report.model_dump_json()


def test_replay_rejects_deletion_reordering_and_checkpoint_anchor_tampering() -> None:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session("ses_trace_attack", _budget())
    harness.transition(session.session_id, SessionState.INGESTING)
    harness.transition(session.session_id, SessionState.ANALYZING)
    spans = harness.traces.list_for(session.trace_id)
    checkpoints = harness.checkpoints.list_for(session.session_id)

    deleted = TraceReplayer().replay(spans[:-1], checkpoints=checkpoints)
    assert deleted.replayable is False
    assert "checkpoint_trace_span_count_mismatch" in deleted.mismatches

    reordered = TraceReplayer().replay(
        (spans[1], spans[0], *spans[2:]), checkpoints=checkpoints
    )
    assert reordered.replayable is False
    assert "trace_integrity_sequence_mismatch" in reordered.mismatches

    inserted = TraceReplayer().replay(
        (spans[0], spans[0], *spans[1:]), checkpoints=checkpoints
    )
    assert inserted.replayable is False
    assert "trace_integrity_sequence_mismatch" in inserted.mismatches

    forged_checkpoints = list(checkpoints)
    forged_checkpoints[-1] = forged_checkpoints[-1].model_copy(
        update={"trace_head_hash": "sha256:" + "0" * 64}
    )
    forged = TraceReplayer().replay(spans, checkpoints=forged_checkpoints)
    assert forged.replayable is False
    assert "checkpoint_trace_hash_mismatch" in forged.mismatches

    forged_state = list(checkpoints)
    forged_state[-1] = forged_state[-1].model_copy(
        update={"state": SessionState.EXECUTING}
    )
    changed_state = TraceReplayer().replay(spans, checkpoints=forged_state)
    assert changed_state.replayable is False
    assert "checkpoint_integrity_hash_mismatch" in changed_state.mismatches

    reordered_checkpoints = TraceReplayer().replay(
        spans, checkpoints=tuple(reversed(checkpoints))
    )
    assert reordered_checkpoints.replayable is False
    assert "checkpoint_trace_order_mismatch" in reordered_checkpoints.mismatches


def test_restore_rejects_invalid_active_trace_before_any_new_action() -> None:
    source = Harness(now=lambda: NOW)
    session = source.create_session("ses_trace_restore", _budget())
    source.transition(session.session_id, SessionState.INGESTING)
    spans = list(source.traces.list_for(session.trace_id))
    spans[0] = spans[0].model_copy(update={"output_summary": "changed"})

    restored = Harness(now=lambda: NOW)
    with pytest.raises(DevBriefError) as raised:
        restored.restore_session(
            session_id=session.session_id,
            trace_id=session.trace_id,
            state=SessionState.INGESTING,
            budget=source.get_session(session.session_id).budget,
            checkpoints=source.checkpoints.list_for(session.session_id),
            traces=tuple(spans),
        )

    assert raised.value.code is ErrorCode.TRACE_INTEGRITY_FAILED
    with pytest.raises(DevBriefError):
        restored.get_session(session.session_id)


def test_legacy_trace_is_visible_as_unsealed_but_cannot_resume_active_work() -> None:
    legacy = TraceSpan(
        span_id="spn_legacy",
        trace_id="trc_legacy",
        session_id="ses_legacy",
        kind=TraceKind.STATE_TRANSITION,
        state_after=SessionState.AWAITING_APPROVAL,
    )
    report = TraceReplayer().replay([legacy])

    assert report.replayable is True
    assert report.integrity_status is TraceIntegrityStatus.LEGACY_UNSEALED

    restored = Harness(now=lambda: NOW)
    with pytest.raises(DevBriefError) as raised:
        restored.restore_session(
            session_id="ses_legacy",
            trace_id="trc_legacy",
            state=SessionState.AWAITING_APPROVAL,
            budget=_budget(),
            traces=(legacy,),
        )

    assert raised.value.code is ErrorCode.TRACE_INTEGRITY_FAILED
