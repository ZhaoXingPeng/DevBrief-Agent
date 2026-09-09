from __future__ import annotations

from datetime import UTC, datetime, timedelta

from devbrief.application.dispatcher import ToolDispatcher
from devbrief.application.harness import Harness
from devbrief.domain.contracts import (
    ExecutionBudget,
    ExecutionKind,
    SessionState,
    ToolLevel,
    ToolRequest,
    ToolResult,
    ToolSpec,
    TraceKind,
)
from devbrief.domain.errors import ErrorCode
from devbrief.domain.tools import PolicyGate, ToolRegistry

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def budget(**overrides: object) -> ExecutionBudget:
    return ExecutionBudget(
        max_steps=8,
        max_tool_calls=overrides.get("max_tool_calls", 2),  # type: ignore[arg-type]
        deadline_at=overrides.get("deadline_at", NOW + timedelta(minutes=5)),  # type: ignore[arg-type]
        max_model_tokens=100,
        max_cost=2.0,
    )


def spec(
    name: str = "draft_issue",
    level: ToolLevel = ToolLevel.DRAFT_WRITE,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Create a local issue draft",
        level=level,
        execution_kind=ExecutionKind.IN_PROCESS,
        timeout_seconds=2.0,
        handler_key="fake_draft",
        input_schema={"required": ["title"]},
    )


def request(
    tool_name: str = "draft_issue",
    *,
    arguments: dict[str, object] | None = None,
    session_id: str = "ses_dispatch",
) -> ToolRequest:
    return ToolRequest(
        tool_call_id="call_1",
        tool_name=tool_name,
        arguments=arguments or {"title": "Fix auth refresh"},
        session_id=session_id,
        trace_id=f"trc_{session_id}",
    )


def planning_harness(
    *,
    max_tool_calls: int = 2,
    deadline_at: datetime | None = None,
    harness_now: datetime = NOW,
) -> tuple[Harness, str]:
    harness = Harness(now=lambda: harness_now)
    session = harness.create_session(
        "ses_dispatch",
        budget(
            max_tool_calls=max_tool_calls,
            deadline_at=deadline_at or NOW + timedelta(minutes=5),
        ),
    )
    for state in (
        SessionState.INGESTING,
        SessionState.ANALYZING,
        SessionState.PLANNING,
    ):
        session = harness.transition(session.session_id, state)
    return harness, session.trace_id


def test_dispatcher_runs_local_draft_and_records_budget_trace_checkpoint() -> None:
    harness, trace_id = planning_harness()
    called: list[dict[str, object]] = []

    def handler(arguments: dict[str, object]) -> ToolResult:
        called.append(arguments)
        return ToolResult(summary="local draft saved", references=["draft://local/1"])

    dispatcher = ToolDispatcher(
        registry=ToolRegistry([spec()]),
        policy=PolicyGate(ToolRegistry([spec()]), now=lambda: NOW),
        harness=harness,
        handlers={"fake_draft": handler},
    )

    outcome = dispatcher.dispatch(request())

    assert outcome.allowed is True
    assert outcome.result == ToolResult(
        summary="local draft saved", references=["draft://local/1"]
    )
    assert called == [{"title": "Fix auth refresh"}]
    session = harness.get_session("ses_dispatch")
    assert session.budget.consumed_tool_calls == 1
    latest = harness.checkpoints.load_latest("ses_dispatch")
    assert latest is not None
    assert latest.budget_summary.consumed_tool_calls == 1
    assert any(
        span.kind is TraceKind.TOOL_CALL and span.tool_name == "draft_issue"
        for span in harness.traces.list_for(trace_id)
    )


def test_dispatcher_denial_never_invokes_handler_or_consumes_budget() -> None:
    harness, _ = planning_harness()
    called = False

    def handler(_: dict[str, object]) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(summary="should not run")

    dispatcher = ToolDispatcher(
        registry=ToolRegistry([spec()]),
        policy=PolicyGate(ToolRegistry([spec()]), now=lambda: NOW),
        harness=harness,
        handlers={"fake_draft": handler},
    )

    denied = dispatcher.dispatch(request(tool_name="missing"))

    assert denied.allowed is False
    assert denied.error_code == ErrorCode.TOOL_NOT_ALLOWED.value
    assert called is False
    assert harness.get_session("ses_dispatch").budget.consumed_tool_calls == 0


def test_dispatcher_rejects_missing_handler_and_malformed_result() -> None:
    harness, _ = planning_harness()
    missing = ToolDispatcher(
        registry=ToolRegistry([spec()]),
        policy=PolicyGate(ToolRegistry([spec()]), now=lambda: NOW),
        harness=harness,
        handlers={},
    ).dispatch(request())
    assert missing.allowed is False
    assert missing.error_code == ErrorCode.VALIDATION_ERROR.value

    malformed_harness, _ = planning_harness()
    malformed = ToolDispatcher(
        registry=ToolRegistry([spec()]),
        policy=PolicyGate(ToolRegistry([spec()]), now=lambda: NOW),
        harness=malformed_harness,
        handlers={"fake_draft": lambda _: {"bad": "result"}},
    ).dispatch(request())
    assert malformed.allowed is False
    assert malformed.error_code == ErrorCode.VALIDATION_ERROR.value
    assert malformed_harness.get_session("ses_dispatch").budget.consumed_tool_calls == 1


def test_dispatcher_budget_and_deadline_stop_before_handler() -> None:
    cases = (
        (0, NOW + timedelta(minutes=5), NOW, ErrorCode.BUDGET_EXHAUSTED),
        (
            2,
            NOW - timedelta(seconds=1),
            NOW - timedelta(minutes=1),
            ErrorCode.DEADLINE_EXCEEDED,
        ),
    )
    for max_tool_calls, deadline_at, harness_now, expected in cases:
        harness, _ = planning_harness(
            max_tool_calls=max_tool_calls,
            deadline_at=deadline_at,
            harness_now=harness_now,
        )
        called = False

        def handler(_: dict[str, object]) -> ToolResult:
            nonlocal called
            called = True
            return ToolResult(summary="should not run")

        outcome = ToolDispatcher(
            registry=ToolRegistry([spec()]),
            policy=PolicyGate(ToolRegistry([spec()]), now=lambda: NOW),
            harness=harness,
            handlers={"fake_draft": handler},
        ).dispatch(request())
        assert outcome.allowed is False
        assert outcome.error_code == expected.value
        assert called is False
