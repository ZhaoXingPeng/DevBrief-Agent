from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from devbrief.domain.approval import (
    ApprovalGate,
    InMemoryApprovalRepository,
    compute_plan_hash,
)
from devbrief.domain.contracts import (
    Approval,
    ApprovalStatus,
    Plan,
    ToolRequest,
)
from devbrief.domain.errors import DevBriefError, ErrorCode

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def plan(**overrides: object) -> Plan:
    values: dict[str, object] = {
        "title": "Fix auth refresh",
        "body": "Refresh credentials after timeout.",
        "repository": "org/repo",
        "labels": ["bug", "p1"],
        "assignee": None,
        "tool_name": "create_issue",
        "arguments": {"title": "Fix auth refresh", "body": "Evidence-backed"},
    }
    values.update(overrides)
    return Plan.model_validate(values)


def request(
    *,
    approval_id: str | None = "apr_1",
    plan_hash: str | None = None,
    tool_name: str = "create_issue",
) -> ToolRequest:
    return ToolRequest(
        tool_call_id="call_1",
        tool_name=tool_name,
        arguments={"title": "Fix auth refresh", "body": "Evidence-backed"},
        session_id="ses_1",
        trace_id="trc_ses_1",
        approval_id=approval_id,
        idempotency_key="idem_1",
        plan_hash=plan_hash,
    )


def approval(
    *,
    plan_hash: str,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    scope: list[str] | None = None,
    expires_at: datetime = NOW + timedelta(minutes=5),
) -> Approval:
    return Approval(
        approval_id="apr_1",
        plan_hash=plan_hash,
        scope=scope or ["create_issue"],
        approver_id="human_1",
        status=status,
        expires_at=expires_at,
        created_at=NOW,
    )


def test_plan_hash_is_stable_and_changes_when_any_bound_field_changes() -> None:
    original = plan()

    assert compute_plan_hash(original) == compute_plan_hash(original)
    assert compute_plan_hash(original).startswith("sha256:")
    assert compute_plan_hash(original) != compute_plan_hash(plan(labels=["bug"]))
    assert compute_plan_hash(original) != compute_plan_hash(
        plan(arguments={"title": "Different"})
    )


def test_repository_rejects_duplicate_approval_ids() -> None:
    repository = InMemoryApprovalRepository()
    item = approval(plan_hash=compute_plan_hash(plan()))
    repository.save(item)

    with pytest.raises(DevBriefError) as raised:
        repository.save(item)
    assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_approved_matching_scope_and_hash_is_consumed_once() -> None:
    current_plan = plan()
    repository = InMemoryApprovalRepository()
    repository.save(approval(plan_hash=compute_plan_hash(current_plan)))
    gate = ApprovalGate(repository, now=lambda: NOW)

    allowed = gate.check(
        request(plan_hash=compute_plan_hash(current_plan)),
        current_plan,
    )
    assert allowed.allowed is True
    assert allowed.status is ApprovalStatus.CONSUMED

    repeated = gate.check(
        request(plan_hash=compute_plan_hash(current_plan)),
        current_plan,
    )
    assert repeated.allowed is False
    assert repeated.error_code == ErrorCode.POLICY_DENIED.value


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ApprovalStatus.PENDING, ErrorCode.APPROVAL_REQUIRED),
        (ApprovalStatus.REJECTED, ErrorCode.POLICY_DENIED),
        (ApprovalStatus.EXPIRED, ErrorCode.APPROVAL_EXPIRED),
        (ApprovalStatus.CONSUMED, ErrorCode.POLICY_DENIED),
    ],
)
def test_non_approved_statuses_are_rejected(
    status: ApprovalStatus, expected: ErrorCode
) -> None:
    current_plan = plan()
    repository = InMemoryApprovalRepository()
    repository.save(approval(plan_hash=compute_plan_hash(current_plan), status=status))
    decision = ApprovalGate(repository, now=lambda: NOW).check(
        request(plan_hash=compute_plan_hash(current_plan)),
        current_plan,
    )

    assert decision.allowed is False
    assert decision.error_code == expected.value


def test_missing_unknown_expired_scope_and_hash_are_rejected() -> None:
    current_plan = plan()
    digest = compute_plan_hash(current_plan)
    repository = InMemoryApprovalRepository()
    repository.save(
        approval(
            plan_hash=digest,
            expires_at=NOW - timedelta(seconds=1),
        )
    )
    gate = ApprovalGate(repository, now=lambda: NOW)

    missing = gate.check(request(approval_id=None, plan_hash=digest), current_plan)
    assert missing.error_code == ErrorCode.APPROVAL_REQUIRED.value

    unknown = gate.check(
        request(approval_id="apr_unknown", plan_hash=digest), current_plan
    )
    assert unknown.error_code == ErrorCode.APPROVAL_REQUIRED.value

    expired = gate.check(request(plan_hash=digest), current_plan)
    assert expired.error_code == ErrorCode.APPROVAL_EXPIRED.value

    repository = InMemoryApprovalRepository()
    repository.save(approval(plan_hash=digest, scope=["read_issue"]))
    out_of_scope = ApprovalGate(repository, now=lambda: NOW).check(
        request(plan_hash=digest), current_plan
    )
    assert out_of_scope.error_code == ErrorCode.TOOL_NOT_ALLOWED.value

    repository = InMemoryApprovalRepository()
    repository.save(approval(plan_hash="sha256:other"))
    mismatched = ApprovalGate(repository, now=lambda: NOW).check(
        request(plan_hash="sha256:other"), current_plan
    )
    assert mismatched.error_code == ErrorCode.VALIDATION_ERROR.value


def test_action_arguments_must_match_the_approved_plan() -> None:
    current_plan = plan(arguments={"title": "Changed after approval"})
    digest = compute_plan_hash(current_plan)
    repository = InMemoryApprovalRepository()
    repository.save(approval(plan_hash=digest))

    mismatched = ApprovalGate(repository, now=lambda: NOW).check(
        request(plan_hash=digest),
        plan(arguments={"title": "Changed after approval"}),
    )

    assert mismatched.error_code == ErrorCode.VALIDATION_ERROR.value


def test_approval_trace_does_not_include_plan_body_or_request_values() -> None:
    current_plan = plan(body="private secret meeting details")
    digest = compute_plan_hash(current_plan)
    repository = InMemoryApprovalRepository()
    repository.save(approval(plan_hash=digest))
    gate = ApprovalGate(repository, now=lambda: NOW)

    gate.check(request(plan_hash=digest), current_plan)
    trace_text = "\n".join(
        f"{span.input_summary}\n{span.output_summary}" for span in gate.traces
    )
    assert "private secret meeting details" not in trace_text
    assert "Evidence-backed" not in trace_text
    assert digest[:16] in trace_text
