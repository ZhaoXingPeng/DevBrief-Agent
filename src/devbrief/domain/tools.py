from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime

from pydantic import ValidationError

from devbrief.domain.budget import consume_tool_call
from devbrief.domain.contracts import (
    ExecutionBudget,
    SessionState,
    ToolLevel,
    ToolPolicyDecision,
    ToolRequest,
    ToolSpec,
    TraceKind,
    TraceSpan,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.trace import redact_summary

Clock = Callable[[], datetime]


class ToolRegistry:
    """Immutable-in-use canonical registry for locally declared tool specs."""

    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        items = tuple(specs)
        by_name: dict[str, ToolSpec] = {}
        for item in items:
            if item.name in by_name:
                raise DevBriefError(
                    ErrorCode.VALIDATION_ERROR,
                    f"duplicate tool name: {item.name}",
                )
            by_name[item.name] = item
        self._by_name = by_name
        self.specs = items

    @classmethod
    def from_payloads(cls, payloads: Iterable[Mapping[str, object]]) -> ToolRegistry:
        try:
            specs = [ToolSpec.model_validate(payload) for payload in payloads]
        except ValidationError as exc:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "invalid tool spec"
            ) from exc
        return cls(specs)

    def get(self, name: str) -> ToolSpec | None:
        return self._by_name.get(name)


class PolicyGate:
    """Deterministic tool admission check; it never dispatches a handler."""

    def __init__(self, registry: ToolRegistry, *, now: Clock | None = None) -> None:
        self.registry = registry
        self._now = now or (lambda: datetime.now(UTC))
        self._decisions: tuple[ToolPolicyDecision, ...] = ()
        self._traces: tuple[TraceSpan, ...] = ()

    @property
    def decisions(self) -> tuple[ToolPolicyDecision, ...]:
        return self._decisions

    @property
    def traces(self) -> tuple[TraceSpan, ...]:
        return self._traces

    def check(
        self,
        request: ToolRequest,
        *,
        session_id: str,
        trace_id: str,
        state: SessionState,
        budget: ExecutionBudget,
    ) -> ToolPolicyDecision:
        spec = self.registry.get(request.tool_name)
        if spec is None:
            return self._deny(
                request,
                session_id,
                trace_id,
                ToolLevel.PROHIBITED_IN_MVP,
                ErrorCode.TOOL_NOT_ALLOWED,
                "tool is not registered",
            )
        if request.session_id != session_id or (
            request.trace_id is not None and request.trace_id != trace_id
        ):
            return self._deny(
                request,
                session_id,
                trace_id,
                spec.level,
                ErrorCode.POLICY_DENIED,
                "request does not belong to session",
            )
        if state.is_terminal:
            return self._deny(
                request,
                session_id,
                trace_id,
                spec.level,
                ErrorCode.POLICY_DENIED,
                "session is terminal",
            )
        if spec.level is ToolLevel.PROHIBITED_IN_MVP:
            return self._deny(
                request,
                session_id,
                trace_id,
                spec.level,
                ErrorCode.TOOL_NOT_ALLOWED,
                "tool level is prohibited in MVP",
            )
        if spec.level is ToolLevel.EXTERNAL_WRITE:
            if state is not SessionState.EXECUTING:
                return self._deny(
                    request,
                    session_id,
                    trace_id,
                    spec.level,
                    ErrorCode.POLICY_DENIED,
                    "external write requires executing state",
                )
            if not request.approval_id:
                return self._deny(
                    request,
                    session_id,
                    trace_id,
                    spec.level,
                    ErrorCode.APPROVAL_REQUIRED,
                    "external write requires approval",
                )
            if not request.plan_hash or not request.idempotency_key:
                return self._deny(
                    request,
                    session_id,
                    trace_id,
                    spec.level,
                    ErrorCode.VALIDATION_ERROR,
                    "external write requires plan hash and idempotency key",
                )
        if not _allows_state(spec.level, state):
            return self._deny(
                request,
                session_id,
                trace_id,
                spec.level,
                ErrorCode.POLICY_DENIED,
                "tool level is not allowed in current state",
            )
        try:
            updated_budget = consume_tool_call(budget, self._now())
        except DevBriefError as exc:
            return self._deny(
                request,
                session_id,
                trace_id,
                spec.level,
                exc.code,
                exc.message,
            )
        return self._allow(request, session_id, trace_id, spec, updated_budget)

    def _allow(
        self,
        request: ToolRequest,
        session_id: str,
        trace_id: str,
        spec: ToolSpec,
        budget: ExecutionBudget,
    ) -> ToolPolicyDecision:
        decision = ToolPolicyDecision(
            allowed=True,
            tool_name=spec.name,
            tool_level=spec.level,
            reason="tool request admitted",
            budget_after=budget,
        )
        self._record(request, session_id, trace_id, decision)
        return decision

    def _deny(
        self,
        request: ToolRequest,
        session_id: str,
        trace_id: str,
        level: ToolLevel,
        code: ErrorCode,
        reason: str,
    ) -> ToolPolicyDecision:
        decision = ToolPolicyDecision(
            allowed=False,
            tool_name=request.tool_name,
            tool_level=level,
            error_code=code.value,
            reason=redact_summary(reason),
        )
        self._record(request, session_id, trace_id, decision)
        return decision

    def _record(
        self,
        request: ToolRequest,
        session_id: str,
        trace_id: str,
        decision: ToolPolicyDecision,
    ) -> None:
        self._decisions += (decision,)
        reason = redact_summary(decision.reason)
        self._traces += (
            TraceSpan(
                span_id=f"policy_{request.tool_call_id}_{len(self._traces) + 1}",
                trace_id=trace_id,
                session_id=session_id,
                kind=TraceKind.POLICY_CHECK,
                input_summary=(
                    f"tool={request.tool_name}; level={decision.tool_level.value}"
                ),
                output_summary=(f"allowed={decision.allowed}; reason={reason}"),
                error_code=decision.error_code,
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                tool_decision="allowed" if decision.allowed else "denied",
            ),
        )


def _allows_state(level: ToolLevel, state: SessionState) -> bool:
    allowed_states: Mapping[ToolLevel, frozenset[SessionState]] = {
        ToolLevel.READ: frozenset(
            {
                SessionState.ANALYZING,
                SessionState.PLANNING,
                SessionState.AWAITING_APPROVAL,
                SessionState.EXECUTING,
            }
        ),
        ToolLevel.DRAFT_WRITE: frozenset({SessionState.PLANNING}),
        ToolLevel.EXTERNAL_WRITE: frozenset({SessionState.EXECUTING}),
        ToolLevel.PROHIBITED_IN_MVP: frozenset(),
    }
    return state in allowed_states[level]
