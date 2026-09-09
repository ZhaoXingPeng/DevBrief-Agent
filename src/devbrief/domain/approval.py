from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256

from devbrief.domain.contracts import (
    Approval,
    ApprovalDecision,
    ApprovalStatus,
    Plan,
    ToolRequest,
    TraceKind,
    TraceSpan,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.trace import redact_summary

Clock = Callable[[], datetime]


def compute_plan_hash(plan: Plan) -> str:
    """Return a stable digest over every field bound by an approval."""
    canonical = json.dumps(
        plan.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"


class InMemoryApprovalRepository:
    """Small fake repository with explicit one-time approval consumption."""

    def __init__(self) -> None:
        self._items: dict[str, Approval] = {}

    def save(self, approval: Approval) -> None:
        if approval.approval_id in self._items:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                f"approval id already exists: {approval.approval_id}",
            )
        self._items[approval.approval_id] = approval

    def get(self, approval_id: str) -> Approval | None:
        return self._items.get(approval_id)

    def mark_expired(self, approval_id: str) -> Approval:
        approval = self._require(approval_id)
        updated = approval.model_copy(update={"status": ApprovalStatus.EXPIRED})
        self._items[approval_id] = updated
        return updated

    def consume(self, approval_id: str) -> Approval:
        approval = self._require(approval_id)
        if approval.status is not ApprovalStatus.APPROVED:
            raise DevBriefError(
                ErrorCode.POLICY_DENIED,
                "only an approved approval can be consumed",
            )
        updated = approval.model_copy(update={"status": ApprovalStatus.CONSUMED})
        self._items[approval_id] = updated
        return updated

    def _require(self, approval_id: str) -> Approval:
        approval = self.get(approval_id)
        if approval is None:
            raise DevBriefError(
                ErrorCode.APPROVAL_REQUIRED,
                "approval does not exist",
            )
        return approval


class ApprovalGate:
    """Validate and consume one precise approval without dispatching tools."""

    def __init__(
        self,
        repository: InMemoryApprovalRepository,
        *,
        now: Clock | None = None,
    ) -> None:
        self.repository = repository
        self._now = now or (lambda: datetime.now(UTC))
        self._traces: tuple[TraceSpan, ...] = ()

    @property
    def traces(self) -> tuple[TraceSpan, ...]:
        return self._traces

    def check(self, request: ToolRequest, plan: Plan) -> ApprovalDecision:
        expected_hash = compute_plan_hash(plan)
        approval_id = request.approval_id or "approval_missing"
        trace_id = request.trace_id or "trc_unbound"

        if not request.approval_id:
            return self._deny(
                request,
                trace_id,
                approval_id,
                expected_hash,
                ErrorCode.APPROVAL_REQUIRED,
                "approval is required",
            )
        if request.plan_hash != expected_hash:
            return self._deny(
                request,
                trace_id,
                approval_id,
                expected_hash,
                ErrorCode.VALIDATION_ERROR,
                "request plan hash does not match current plan",
            )
        if request.tool_name != plan.tool_name or request.arguments != plan.arguments:
            return self._deny(
                request,
                trace_id,
                approval_id,
                expected_hash,
                ErrorCode.VALIDATION_ERROR,
                "request action does not match approved plan",
            )

        approval = self.repository.get(request.approval_id)
        if approval is None:
            return self._deny(
                request,
                trace_id,
                approval_id,
                expected_hash,
                ErrorCode.APPROVAL_REQUIRED,
                "approval does not exist",
            )
        if approval.plan_hash != expected_hash:
            return self._deny(
                request,
                trace_id,
                approval_id,
                expected_hash,
                ErrorCode.VALIDATION_ERROR,
                "approval plan hash does not match current plan",
            )
        if request.tool_name not in approval.scope:
            return self._deny(
                request,
                trace_id,
                approval_id,
                expected_hash,
                ErrorCode.TOOL_NOT_ALLOWED,
                "approval scope does not include requested tool",
            )
        if approval.status is ApprovalStatus.APPROVED:
            if self._now() >= approval.expires_at:
                self.repository.mark_expired(approval.approval_id)
                return self._deny(
                    request,
                    trace_id,
                    approval_id,
                    expected_hash,
                    ErrorCode.APPROVAL_EXPIRED,
                    "approval has expired",
                    status=ApprovalStatus.EXPIRED,
                )
            consumed = self.repository.consume(approval.approval_id)
            return self._allow(request, trace_id, consumed, expected_hash)
        if approval.status is ApprovalStatus.PENDING:
            code = ErrorCode.APPROVAL_REQUIRED
            reason = "approval is not approved"
        elif approval.status is ApprovalStatus.EXPIRED:
            code = ErrorCode.APPROVAL_EXPIRED
            reason = "approval has expired"
        else:
            code = ErrorCode.POLICY_DENIED
            reason = "approval is not usable"
        return self._deny(
            request,
            trace_id,
            approval_id,
            expected_hash,
            code,
            reason,
            status=approval.status,
        )

    def _allow(
        self,
        request: ToolRequest,
        trace_id: str,
        approval: Approval,
        plan_hash: str,
    ) -> ApprovalDecision:
        decision = ApprovalDecision(
            allowed=True,
            approval_id=approval.approval_id,
            plan_hash=plan_hash,
            status=approval.status,
            reason="approval consumed",
        )
        self._record(request, trace_id, decision)
        return decision

    def _deny(
        self,
        request: ToolRequest,
        trace_id: str,
        approval_id: str,
        plan_hash: str,
        code: ErrorCode,
        reason: str,
        *,
        status: ApprovalStatus | None = None,
    ) -> ApprovalDecision:
        decision = ApprovalDecision(
            allowed=False,
            approval_id=approval_id,
            plan_hash=plan_hash,
            status=status,
            error_code=code.value,
            reason=redact_summary(reason),
        )
        self._record(request, trace_id, decision)
        return decision

    def _record(
        self,
        request: ToolRequest,
        trace_id: str,
        decision: ApprovalDecision,
    ) -> None:
        plan_digest = decision.plan_hash[:16]
        self._traces += (
            TraceSpan(
                span_id=f"approval_{decision.approval_id}_{len(self._traces) + 1}",
                trace_id=trace_id,
                session_id=request.session_id,
                kind=TraceKind.APPROVAL,
                input_summary=(
                    f"approval={decision.approval_id}; tool={request.tool_name}; "
                    f"plan={plan_digest}"
                ),
                output_summary=(
                    f"allowed={decision.allowed}; "
                    f"reason={redact_summary(decision.reason)}"
                ),
                error_code=decision.error_code,
                plan_hash=decision.plan_hash,
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
            ),
        )
