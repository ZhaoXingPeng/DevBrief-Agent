from __future__ import annotations

from datetime import UTC, datetime, timedelta

from devbrief.application.external_write import ControlledIssueWriter, FakeIssueProvider
from devbrief.application.harness import Harness
from devbrief.application.receipts import InMemoryReceiptRepository
from devbrief.domain.approval import (
    ApprovalGate,
    InMemoryApprovalRepository,
    compute_plan_hash,
)
from devbrief.domain.contracts import (
    Approval,
    ApprovalStatus,
    ExecutionBudget,
    ExecutionKind,
    Plan,
    SessionState,
    ToolLevel,
    ToolReceiptStatus,
    ToolRequest,
    ToolSpec,
    TraceKind,
)
from devbrief.domain.errors import ErrorCode
from devbrief.domain.tools import PolicyGate, ToolRegistry

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def plan(*, body: str = "Evidence-backed") -> Plan:
    return Plan(
        title="Fix auth refresh",
        body=body,
        repository="org/repo",
        labels=["bug"],
        assignee=None,
        tool_name="create_issue",
        arguments={"title": "Fix auth refresh", "body": body},
    )


def request(
    *,
    body: str = "Evidence-backed",
    key: str = "idem-1",
    tool_call_id: str | None = None,
) -> ToolRequest:
    return ToolRequest(
        tool_call_id=tool_call_id or f"call-{key}",
        tool_name="create_issue",
        arguments={"title": "Fix auth refresh", "body": body},
        session_id="ses_write",
        trace_id="trc_ses_write",
        approval_id="apr-1",
        idempotency_key=key,
        plan_hash=compute_plan_hash(plan(body=body)),
    )


def executing_harness() -> Harness:
    harness = Harness(now=lambda: NOW)
    session = harness.create_session(
        "ses_write",
        ExecutionBudget(
            max_steps=8,
            max_tool_calls=2,
            deadline_at=NOW + timedelta(minutes=5),
            max_model_tokens=100,
            max_cost=2.0,
        ),
    )
    for state in (
        SessionState.INGESTING,
        SessionState.ANALYZING,
        SessionState.PLANNING,
        SessionState.AWAITING_APPROVAL,
        SessionState.EXECUTING,
    ):
        session = harness.transition(session.session_id, state)
    return harness


def writer_setup(
    *,
    provider: FakeIssueProvider | None = None,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    body: str = "Evidence-backed",
    tool_level: ToolLevel = ToolLevel.EXTERNAL_WRITE,
) -> tuple[ControlledIssueWriter, FakeIssueProvider, Harness]:
    harness = executing_harness()
    registry = ToolRegistry(
        [
            ToolSpec(
                name="create_issue",
                description="Create a fake issue",
                level=tool_level,
                execution_kind=ExecutionKind.IN_PROCESS,
                timeout_seconds=2.0,
                handler_key="fake_issue",
                input_schema={"required": ["title", "body"]},
            )
        ]
    )
    provider = provider or FakeIssueProvider(now=lambda: NOW)
    approvals = InMemoryApprovalRepository()
    current_plan = plan(body=body)
    approvals.save(
        Approval(
            approval_id="apr-1",
            plan_hash=compute_plan_hash(current_plan),
            scope=["create_issue"],
            approver_id="human-1",
            status=approval_status,
            expires_at=NOW + timedelta(minutes=5),
            created_at=NOW,
        )
    )
    return (
        ControlledIssueWriter(
            registry=registry,
            policy=PolicyGate(registry, now=lambda: NOW),
            approval_gate=ApprovalGate(approvals, now=lambda: NOW),
            receipt_repository=InMemoryReceiptRepository(),
            provider=provider,
            harness=harness,
        ),
        provider,
        harness,
    )


def test_controlled_writer_requires_policy_approval_and_prewrite_checkpoint() -> None:
    writer, provider, harness = writer_setup()

    outcome = writer.execute(request(), plan())

    assert outcome.allowed is True
    assert outcome.receipt is not None
    assert outcome.receipt.status is ToolReceiptStatus.SUCCEEDED
    assert provider.create_calls == 1
    checkpoints = harness.checkpoints.list_for("ses_write")
    assert any(
        checkpoint.plan_hash == compute_plan_hash(plan())
        and checkpoint.approval_id == "apr-1"
        and checkpoint.idempotency_keys == ["idem-1"]
        for checkpoint in checkpoints
    )
    assert any(
        span.kind is TraceKind.POLICY_CHECK
        for span in harness.traces.list_for("trc_ses_write")
    )
    assert any(span.kind is TraceKind.APPROVAL for span in writer.approval_gate.traces)


def test_denied_approval_never_calls_provider() -> None:
    writer, provider, _ = writer_setup(approval_status=ApprovalStatus.PENDING)

    outcome = writer.execute(request(), plan())

    assert outcome.allowed is False
    assert outcome.error_code == ErrorCode.APPROVAL_REQUIRED.value
    assert provider.create_calls == 0


def test_non_external_tool_never_reaches_fake_provider() -> None:
    writer, provider, _ = writer_setup(tool_level=ToolLevel.DRAFT_WRITE)

    outcome = writer.execute(request(), plan())

    assert outcome.allowed is False
    assert outcome.error_code == ErrorCode.POLICY_DENIED.value
    assert provider.create_calls == 0


def test_duplicate_idempotency_returns_original_receipt_without_second_write() -> None:
    writer, provider, _ = writer_setup()
    first = writer.execute(request(), plan())
    repeated = writer.execute(request(tool_call_id="call-replay"), plan())

    assert first.receipt is not None
    assert repeated.receipt == first.receipt
    assert provider.create_calls == 1


def test_unknown_provider_result_is_query_first_and_never_blind_retried() -> None:
    provider = FakeIssueProvider(
        now=lambda: NOW, next_status=ToolReceiptStatus.UNKNOWN_OUTCOME
    )
    writer, provider, _ = writer_setup(provider=provider)
    original = writer.execute(request(), plan())

    assert original.receipt is not None
    assert original.receipt.status is ToolReceiptStatus.UNKNOWN_OUTCOME
    assert provider.create_calls == 1

    recovered_without_confirmation = writer.recover(request())
    assert recovered_without_confirmation.receipt == original.receipt
    assert provider.create_calls == 1
    assert provider.query_calls == 1

    provider.confirm(request(), external_object_id="issue-42")
    recovered = writer.recover(request())
    assert recovered.receipt is not None
    assert recovered.receipt.status is ToolReceiptStatus.SUCCEEDED
    assert recovered.receipt.external_object_id == "issue-42"
    assert provider.create_calls == 1
    assert provider.query_calls == 2


def test_plan_hash_or_state_mismatch_blocks_provider() -> None:
    writer, provider, harness = writer_setup()
    changed_plan = plan(body="Changed after approval")
    changed_request = request(body="Changed after approval")

    outcome = writer.execute(changed_request, changed_plan)

    assert outcome.allowed is False
    assert outcome.error_code == ErrorCode.VALIDATION_ERROR.value
    assert provider.create_calls == 0
    harness.cancel("ses_write")
    cancelled = writer.execute(request(), plan())
    assert cancelled.allowed is False
    assert cancelled.error_code == ErrorCode.POLICY_DENIED.value


def test_write_trace_redacts_sensitive_plan_text() -> None:
    private_body = "token=private-value; evidence body"
    writer, _, harness = writer_setup(body=private_body)
    outcome = writer.execute(request(body=private_body), plan(body=private_body))

    assert outcome.allowed is True
    trace_text = "\n".join(
        f"{span.input_summary}\n{span.output_summary}"
        for span in harness.traces.list_for("trc_ses_write")
    )
    assert "private-value" not in trace_text
