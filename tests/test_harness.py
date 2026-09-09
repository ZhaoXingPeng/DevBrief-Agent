from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from devbrief.application.harness import Harness
from devbrief.domain.contracts import ExecutionBudget, SessionState
from devbrief.domain.errors import DevBriefError, ErrorCode

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def budget(**overrides: object) -> ExecutionBudget:
    return ExecutionBudget(
        max_steps=overrides.get("max_steps", 8),  # type: ignore[arg-type]
        max_tool_calls=overrides.get("max_tool_calls", 2),  # type: ignore[arg-type]
        deadline_at=overrides.get("deadline_at", NOW + timedelta(minutes=5)),  # type: ignore[arg-type]
        max_model_tokens=overrides.get("max_model_tokens", 100),  # type: ignore[arg-type]
        max_cost=overrides.get("max_cost", 2.0),  # type: ignore[arg-type]
    )


def test_harness_allows_normal_lifecycle_and_blocks_terminal_transition() -> None:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session("ses_normal", budget())

    for state in (
        SessionState.INGESTING,
        SessionState.ANALYZING,
        SessionState.PLANNING,
        SessionState.AWAITING_APPROVAL,
        SessionState.EXECUTING,
        SessionState.COMPLETED,
    ):
        session = harness.transition(session.session_id, state)

    assert session.state is SessionState.COMPLETED
    assert session.budget.consumed_steps == 6
    with pytest.raises(DevBriefError, match="terminal"):
        harness.transition(session.session_id, SessionState.ANALYZING)


def test_harness_rejects_illegal_transition_without_consuming_budget() -> None:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session("ses_illegal", budget())

    with pytest.raises(DevBriefError) as raised:
        harness.transition(session.session_id, SessionState.PLANNING)

    assert raised.value.code is ErrorCode.VALIDATION_ERROR
    assert harness.get_session(session.session_id).budget.consumed_steps == 0


def test_step_budget_stops_work_and_retains_last_safe_checkpoint() -> None:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session("ses_steps", budget(max_steps=1))
    harness.transition(session.session_id, SessionState.INGESTING)

    with pytest.raises(DevBriefError) as raised:
        harness.transition(session.session_id, SessionState.ANALYZING)

    assert raised.value.code is ErrorCode.BUDGET_EXHAUSTED
    assert harness.get_session(session.session_id).state is SessionState.FAILED_TERMINAL
    checkpoint = harness.checkpoints.load_latest(session.session_id)
    assert checkpoint is not None
    assert checkpoint.state is SessionState.INGESTING


def test_deadline_stops_recoverably_and_resume_uses_safe_checkpoint() -> None:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session("ses_resume", budget())
    session = harness.transition(session.session_id, SessionState.INGESTING)
    session = harness.transition(session.session_id, SessionState.ANALYZING)
    harness.fail_recoverable(session.session_id, "transient_provider_error")

    resumed = harness.resume(session.session_id)

    assert resumed.state is SessionState.ANALYZING
    assert resumed.budget.consumed_steps == 2

    expired = Harness(now=lambda: NOW)
    expired_session = expired.create_session(
        "ses_deadline", budget(deadline_at=NOW - timedelta(seconds=1))
    )
    with pytest.raises(DevBriefError) as raised:
        expired.transition(expired_session.session_id, SessionState.INGESTING)
    assert raised.value.code is ErrorCode.DEADLINE_EXCEEDED
    assert (
        expired.get_session(expired_session.session_id).state
        is SessionState.FAILED_RECOVERABLE
    )


def test_model_token_and_cost_budgets_stop_future_work() -> None:
    harness = Harness(now=lambda: NOW)
    token_session = harness.create_session("ses_tokens", budget(max_model_tokens=4))
    with pytest.raises(DevBriefError) as tokens:
        harness.record_model_usage(token_session.session_id, tokens=5, cost=0.0)
    assert tokens.value.code is ErrorCode.BUDGET_EXHAUSTED
    assert (
        harness.get_session(token_session.session_id).state
        is SessionState.FAILED_TERMINAL
    )

    cost_session = harness.create_session("ses_cost", budget(max_cost=0.5))
    with pytest.raises(DevBriefError) as cost:
        harness.record_model_usage(cost_session.session_id, tokens=1, cost=0.6)
    assert cost.value.code is ErrorCode.COST_EXHAUSTED
    assert (
        harness.get_session(cost_session.session_id).state
        is SessionState.FAILED_TERMINAL
    )


def test_cancel_is_terminal_and_trace_retains_only_metadata() -> None:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session("ses_cancel", budget())
    harness.record_ingest(
        session.session_id,
        fixture_id="bug-triage-redacted-v1",
        version="1.0.0",
        segments=2,
    )

    cancelled = harness.cancel(session.session_id)

    assert cancelled.state is SessionState.CANCELLED
    with pytest.raises(DevBriefError):
        harness.resume(session.session_id)
    trace_text = "\n".join(
        f"{span.input_summary}\n{span.output_summary}"
        for span in harness.traces.list_for(session.trace_id)
    )
    assert "fixture=bug-triage-redacted-v1" in trace_text
    assert "Private synthetic meeting sentence." not in trace_text
    assert "token=private-value" not in trace_text


def test_recoverable_failure_redacts_sensitive_error_detail() -> None:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session("ses_secret_error", budget())

    harness.fail_recoverable(session.session_id, "token=private-value")

    latest = harness.traces.list_for(session.trace_id)[-1]
    assert latest.error_code == "token=[REDACTED]"
