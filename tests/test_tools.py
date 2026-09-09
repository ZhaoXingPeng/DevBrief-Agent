from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from devbrief.domain.contracts import (
    ExecutionBudget,
    ExecutionKind,
    SessionState,
    ToolLevel,
    ToolRequest,
    ToolSpec,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.tools import PolicyGate, ToolRegistry

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def budget(**overrides: object) -> ExecutionBudget:
    return ExecutionBudget(
        max_steps=8,
        max_tool_calls=overrides.get("max_tool_calls", 2),  # type: ignore[arg-type]
        deadline_at=overrides.get("deadline_at", NOW + timedelta(minutes=5)),  # type: ignore[arg-type]
        max_model_tokens=100,
        max_cost=2.0,
        consumed_tool_calls=overrides.get("consumed_tool_calls", 0),  # type: ignore[arg-type]
    )


def spec(name: str = "search_code", level: ToolLevel = ToolLevel.READ) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Search repository evidence",
        level=level,
        execution_kind=ExecutionKind.IN_PROCESS,
        timeout_seconds=2.0,
        handler_key=f"fake_{name}",
    )


def request(
    tool_name: str = "search_code",
    *,
    session_id: str = "ses_1",
    approval_id: str | None = None,
    idempotency_key: str | None = None,
    plan_hash: str | None = None,
    arguments: dict[str, object] | None = None,
) -> ToolRequest:
    return ToolRequest(
        tool_call_id="call_1",
        tool_name=tool_name,
        arguments=arguments or {"query": "auth"},
        session_id=session_id,
        trace_id="trc_ses_1",
        approval_id=approval_id,
        idempotency_key=idempotency_key,
        plan_hash=plan_hash,
    )


def test_registry_returns_canonical_specs_and_rejects_duplicates() -> None:
    registry = ToolRegistry([spec(), spec("draft_issue", ToolLevel.DRAFT_WRITE)])

    assert registry.get("search_code") == spec()
    assert [item.name for item in registry.specs] == ["search_code", "draft_issue"]
    with pytest.raises(DevBriefError) as raised:
        ToolRegistry([spec(), spec()])
    assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_registry_payload_validation_rejects_invalid_name_and_missing_handler() -> None:
    with pytest.raises(DevBriefError) as invalid_name:
        ToolRegistry.from_payloads(
            [
                {
                    "name": "Search-Code",
                    "description": "bad",
                    "level": "read",
                    "execution_kind": "in_process",
                    "timeout_seconds": 1,
                    "handler_key": "fake",
                }
            ]
        )
    assert invalid_name.value.code is ErrorCode.VALIDATION_ERROR

    with pytest.raises(DevBriefError) as missing_handler:
        ToolRegistry.from_payloads(
            [
                {
                    "name": "search_code",
                    "description": "bad",
                    "level": "read",
                    "execution_kind": "in_process",
                    "timeout_seconds": 1,
                    "handler_key": "",
                }
            ]
        )
    assert missing_handler.value.code is ErrorCode.VALIDATION_ERROR

    with pytest.raises(DevBriefError) as invalid_timeout:
        ToolRegistry.from_payloads(
            [
                {
                    "name": "search_code",
                    "description": "bad",
                    "level": "read",
                    "execution_kind": "in_process",
                    "timeout_seconds": 0,
                    "handler_key": "fake",
                }
            ]
        )
    assert invalid_timeout.value.code is ErrorCode.VALIDATION_ERROR


def test_policy_allows_read_and_consumes_tool_budget_without_execution() -> None:
    gate = PolicyGate(ToolRegistry([spec()]), now=lambda: NOW)

    decision = gate.check(
        request(),
        session_id="ses_1",
        trace_id="trc_ses_1",
        state=SessionState.ANALYZING,
        budget=budget(),
    )

    assert decision.allowed is True
    assert decision.error_code is None
    assert decision.budget_after is not None
    assert decision.budget_after.consumed_tool_calls == 1
    assert gate.decisions == (decision,)


def test_tool_request_trace_id_is_backward_compatible_when_omitted() -> None:
    legacy_request = ToolRequest(
        tool_call_id="call_legacy",
        tool_name="search_code",
        session_id="ses_1",
    )

    assert legacy_request.trace_id is None


def test_policy_rejects_unknown_prohibited_mismatched_and_exhausted_requests() -> None:
    gate = PolicyGate(
        ToolRegistry(
            [
                spec(),
                spec("run_shell", ToolLevel.PROHIBITED_IN_MVP),
            ]
        ),
        now=lambda: NOW,
    )

    denied = [
        (request("missing"), ErrorCode.TOOL_NOT_ALLOWED),
        (request("run_shell"), ErrorCode.TOOL_NOT_ALLOWED),
        (request(session_id="ses_other"), ErrorCode.POLICY_DENIED),
        (request(), ErrorCode.BUDGET_EXHAUSTED),
    ]
    for tool_request, code in denied:
        current_budget = (
            budget(max_tool_calls=0) if code is ErrorCode.BUDGET_EXHAUSTED else budget()
        )
        decision = gate.check(
            tool_request,
            session_id="ses_1",
            trace_id="trc_ses_1",
            state=SessionState.ANALYZING,
            budget=current_budget,
        )
        assert decision.allowed is False
        assert decision.error_code == code.value
        assert decision.budget_after is None


def test_policy_denials_do_not_consume_budget_and_draft_is_planning_only() -> None:
    gate = PolicyGate(
        ToolRegistry([spec("draft_issue", ToolLevel.DRAFT_WRITE)]),
        now=lambda: NOW,
    )
    initial = budget(max_tool_calls=1)

    denied = gate.check(
        request("draft_issue"),
        session_id="ses_1",
        trace_id="trc_ses_1",
        state=SessionState.ANALYZING,
        budget=initial,
    )
    assert denied.error_code == ErrorCode.POLICY_DENIED.value
    assert denied.budget_after is None
    assert initial.consumed_tool_calls == 0

    allowed = gate.check(
        request("draft_issue"),
        session_id="ses_1",
        trace_id="trc_ses_1",
        state=SessionState.PLANNING,
        budget=initial,
    )
    assert allowed.allowed is True
    assert allowed.budget_after is not None
    assert allowed.budget_after.consumed_tool_calls == 1


def test_external_write_requires_exact_approval_fields_and_executing_state() -> None:
    gate = PolicyGate(
        ToolRegistry([spec("create_issue", ToolLevel.EXTERNAL_WRITE)]),
        now=lambda: NOW,
    )

    missing = gate.check(
        request("create_issue"),
        session_id="ses_1",
        trace_id="trc_ses_1",
        state=SessionState.EXECUTING,
        budget=budget(),
    )
    assert missing.error_code == ErrorCode.APPROVAL_REQUIRED.value

    allowed = gate.check(
        request(
            "create_issue",
            approval_id="apr_1",
            idempotency_key="idem_1",
            plan_hash="sha256:plan",
        ),
        session_id="ses_1",
        trace_id="trc_ses_1",
        state=SessionState.EXECUTING,
        budget=budget(),
    )
    assert allowed.allowed is True

    awaiting = gate.check(
        request(
            "create_issue",
            approval_id="apr_1",
            idempotency_key="idem_1",
            plan_hash="sha256:plan",
        ),
        session_id="ses_1",
        trace_id="trc_ses_1",
        state=SessionState.AWAITING_APPROVAL,
        budget=budget(),
    )
    assert awaiting.error_code == ErrorCode.POLICY_DENIED.value


def test_policy_trace_never_retains_request_secrets_and_deadline_is_rejected() -> None:
    gate = PolicyGate(ToolRegistry([spec()]), now=lambda: NOW)

    decision = gate.check(
        request(arguments={"token": "private-value"}),
        session_id="ses_1",
        trace_id="trc_ses_1",
        state=SessionState.ANALYZING,
        budget=budget(deadline_at=NOW - timedelta(seconds=1)),
    )

    assert decision.error_code == ErrorCode.DEADLINE_EXCEEDED.value
    trace_text = "\n".join(
        f"{span.input_summary}\n{span.output_summary}" for span in gate.traces
    )
    assert "private-value" not in trace_text
    assert "token" not in trace_text
