from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from pydantic import ValidationError

from devbrief.application.harness import Harness
from devbrief.domain.contracts import (
    ToolDispatchOutcome,
    ToolRequest,
    ToolResult,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.tools import PolicyGate, ToolRegistry
from devbrief.domain.trace import redact_summary

ToolHandler = Callable[[dict[str, object]], object]


class ToolDispatcher:
    """Dispatch registered local tools only after deterministic policy admission."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyGate,
        harness: Harness,
        handlers: Mapping[str, ToolHandler],
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.harness = harness
        self.handlers = handlers

    def dispatch(self, request: ToolRequest) -> ToolDispatchOutcome:
        """Validate, authorize, execute and checkpoint one in-process tool call."""
        session = self.harness.get_session(request.session_id)
        spec = self.registry.get(request.tool_name)
        if spec is None:
            return self._denied(
                request, ErrorCode.TOOL_NOT_ALLOWED, "tool is not registered"
            )
        try:
            _validate_arguments(spec.input_schema, request.arguments)
        except (TypeError, ValueError, ValidationError) as exc:
            return self._denied(
                request, ErrorCode.POLICY_DENIED, "tool arguments are invalid", exc
            )

        decision = self.policy.check(
            request,
            session_id=session.session_id,
            trace_id=session.trace_id,
            state=session.state,
            budget=session.budget,
        )
        if self.policy.traces:
            self.harness.append_trace(self.policy.traces[-1])
        if not decision.allowed or decision.budget_after is None:
            code = ErrorCode(decision.error_code or ErrorCode.POLICY_DENIED.value)
            return self._denied(request, code, decision.reason)

        handler = self.handlers.get(spec.handler_key)
        if handler is None:
            self.harness.record_tool_call(
                session.session_id,
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                budget_after=decision.budget_after,
                output_summary="handler missing",
                error_code=ErrorCode.VALIDATION_ERROR,
            )
            return self._denied(
                request, ErrorCode.VALIDATION_ERROR, "tool handler is not registered"
            )
        try:
            result = ToolResult.model_validate(handler(request.arguments))
        except DevBriefError as exc:
            self.harness.record_tool_call(
                session.session_id,
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                budget_after=decision.budget_after,
                output_summary="handler domain error",
                error_code=exc.code,
            )
            return self._denied(request, exc.code, exc.message)
        except (TypeError, ValueError, ValidationError) as exc:
            self.harness.record_tool_call(
                session.session_id,
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                budget_after=decision.budget_after,
                output_summary="handler result validation failed",
                error_code=ErrorCode.VALIDATION_ERROR,
            )
            return self._denied(
                request, ErrorCode.VALIDATION_ERROR, "tool result is invalid", exc
            )

        self.harness.record_tool_call(
            session.session_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            budget_after=decision.budget_after,
            output_summary=redact_summary(result.summary),
        )
        return ToolDispatchOutcome(
            allowed=True,
            tool_name=request.tool_name,
            result=result,
            reason="tool executed",
        )

    def _denied(
        self,
        request: ToolRequest,
        code: ErrorCode,
        reason: str,
        cause: BaseException | None = None,
    ) -> ToolDispatchOutcome:
        del cause
        return ToolDispatchOutcome(
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
