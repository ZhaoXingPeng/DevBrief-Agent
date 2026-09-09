from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import cast

from devbrief.application.harness import Harness
from devbrief.application.receipts import (
    InMemoryReceiptRepository,
    ReceiptQueryAdapter,
    ReceiptRecoveryService,
)
from devbrief.domain.approval import ApprovalGate
from devbrief.domain.contracts import (
    ExternalWriteOutcome,
    Plan,
    ToolLevel,
    ToolReceipt,
    ToolReceiptStatus,
    ToolRequest,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.tools import PolicyGate, ToolRegistry
from devbrief.domain.trace import redact_summary

Clock = Callable[[], datetime]


class FakeIssueProvider(ReceiptQueryAdapter):
    """In-memory provider fake that models success and unknown write outcomes."""

    def __init__(
        self,
        *,
        now: Clock | None = None,
        next_status: ToolReceiptStatus = ToolReceiptStatus.SUCCEEDED,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self.next_status = next_status
        self.create_calls = 0
        self.query_calls = 0
        self._confirmed: dict[str, ToolReceipt] = {}

    def create(self, request: ToolRequest) -> ToolReceipt:
        if not request.idempotency_key:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "idempotency key is required"
            )
        self.create_calls += 1
        status = self.next_status
        return ToolReceipt(
            receipt_id=f"rcpt_fake_{self.create_calls}",
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            status=status,
            external_object_id=(
                f"issue-{self.create_calls}"
                if status is ToolReceiptStatus.SUCCEEDED
                else None
            ),
            external_url=(
                f"https://fake.invalid/issues/{self.create_calls}"
                if status is ToolReceiptStatus.SUCCEEDED
                else None
            ),
            provider_request_id=f"provider-{self.create_calls}",
            idempotency_key=request.idempotency_key,
            created_at=self._now(),
        )

    def confirm(self, request: ToolRequest, *, external_object_id: str) -> None:
        if not request.idempotency_key:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "idempotency key is required"
            )
        self._confirmed[request.idempotency_key] = ToolReceipt(
            receipt_id="rcpt_confirmed",
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            status=ToolReceiptStatus.SUCCEEDED,
            external_object_id=external_object_id,
            external_url=f"https://fake.invalid/{external_object_id}",
            provider_request_id="provider-confirmed",
            idempotency_key=request.idempotency_key,
            created_at=self._now(),
        )

    def query(self, request: ToolRequest) -> ToolReceipt | None:
        self.query_calls += 1
        if not request.idempotency_key:
            return None
        return self._confirmed.get(request.idempotency_key)


class ControlledIssueWriter:
    """Execute fake writes with policy, approval, checkpoint and receipt."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyGate,
        approval_gate: ApprovalGate,
        receipt_repository: InMemoryReceiptRepository,
        provider: FakeIssueProvider,
        harness: Harness,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.approval_gate = approval_gate
        self.receipt_repository = receipt_repository
        self.provider = provider
        self.harness = harness

    def execute(self, request: ToolRequest, plan: Plan) -> ExternalWriteOutcome:
        """Perform one approved fake write or return a structured denial."""
        try:
            existing = self.receipt_repository.find(request)
        except DevBriefError as exc:
            return self._denied(request, exc.code, exc.message)
        if existing is not None:
            return ExternalWriteOutcome(
                allowed=True,
                tool_name=request.tool_name,
                receipt=existing,
                reason="idempotent receipt replay",
            )

        session = self.harness.get_session(request.session_id)
        spec = self.registry.get(request.tool_name)
        if spec is None:
            return self._denied(
                request, ErrorCode.TOOL_NOT_ALLOWED, "tool is not registered"
            )
        if spec.level is not ToolLevel.EXTERNAL_WRITE:
            return self._denied(
                request,
                ErrorCode.POLICY_DENIED,
                "tool is not an external write",
            )
        try:
            _validate_arguments(spec.input_schema, request.arguments)
        except ValueError as exc:
            return self._denied(request, ErrorCode.VALIDATION_ERROR, str(exc))
        policy = self.policy.check(
            request,
            session_id=session.session_id,
            trace_id=session.trace_id,
            state=session.state,
            budget=session.budget,
        )
        self._mirror_policy_trace()
        if not policy.allowed or policy.budget_after is None:
            code = ErrorCode(policy.error_code or ErrorCode.POLICY_DENIED.value)
            return self._denied(request, code, policy.reason)

        approval = self.approval_gate.check(request, plan)
        self._mirror_approval_trace()
        if not approval.allowed:
            code = ErrorCode(approval.error_code or ErrorCode.POLICY_DENIED.value)
            return self._denied(request, code, approval.reason)

        self.harness.record_external_intent(
            session.session_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            budget_after=policy.budget_after,
            plan_hash=approval.plan_hash,
            approval_id=approval.approval_id,
            idempotency_key=request.idempotency_key or "",
        )
        try:
            provider_receipt = self.provider.create(request)
            saved = self.receipt_repository.save(request, provider_receipt)
        except DevBriefError as exc:
            return self._denied(request, exc.code, exc.message)
        self.harness.record_tool_result(
            session.session_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            output_summary=f"receipt={saved.receipt_id}; status={saved.status.value}",
            plan_hash=approval.plan_hash,
            approval_id=approval.approval_id,
            idempotency_key=request.idempotency_key,
            receipt_id=saved.receipt_id,
        )
        return ExternalWriteOutcome(
            allowed=True,
            tool_name=request.tool_name,
            receipt=saved,
            reason="fake external write completed",
        )

    def recover(self, request: ToolRequest) -> ExternalWriteOutcome:
        """Query an unknown receipt without consuming approval again."""
        service = ReceiptRecoveryService(self.receipt_repository, self.provider)
        try:
            receipt = service.recover(request)
        except DevBriefError as exc:
            return self._denied(request, exc.code, exc.message)
        for trace in service.traces:
            self.harness.append_trace(trace)
        return ExternalWriteOutcome(
            allowed=True,
            tool_name=request.tool_name,
            receipt=receipt,
            reason="receipt recovered by query",
        )

    def _mirror_policy_trace(self) -> None:
        if self.policy.traces:
            self.harness.append_trace(self.policy.traces[-1])

    def _mirror_approval_trace(self) -> None:
        if self.approval_gate.traces:
            self.harness.append_trace(self.approval_gate.traces[-1])

    @staticmethod
    def _denied(
        request: ToolRequest, code: ErrorCode, reason: str
    ) -> ExternalWriteOutcome:
        return ExternalWriteOutcome(
            allowed=False,
            tool_name=request.tool_name,
            error_code=code.value,
            reason=redact_summary(reason),
        )


def _validate_arguments(
    schema: Mapping[str, object], arguments: Mapping[str, object]
) -> None:
    required = schema.get("required", [])
    if not isinstance(required, list):
        raise ValueError("tool schema required must be a list of strings")
    required_items = cast(list[object], required)
    required_names = [item for item in required_items if isinstance(item, str)]
    if len(required_names) != len(required_items):
        raise ValueError("tool schema required must be a list of strings")
    missing = [item for item in required_names if item not in arguments]
    if missing:
        raise ValueError("required tool arguments are missing")
