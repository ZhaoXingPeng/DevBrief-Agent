from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    POLICY_DENIED = "policy_denied"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_EXPIRED = "approval_expired"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    COST_EXHAUSTED = "cost_exhausted"
    CANCELLED = "cancelled"
    CHECKPOINT_UNAVAILABLE = "checkpoint_unavailable"
    UNKNOWN_OUTCOME = "unknown_outcome"
    INTERNAL_ERROR = "internal_error"


class DevBriefError(Exception):
    """Expected domain failure with a stable, serializable error code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
