from __future__ import annotations

from collections.abc import Mapping

from devbrief.domain.contracts import SessionState
from devbrief.domain.errors import DevBriefError, ErrorCode

_TRANSITIONS: Mapping[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset({SessionState.INGESTING}),
    SessionState.INGESTING: frozenset({SessionState.ANALYZING}),
    SessionState.ANALYZING: frozenset({SessionState.PLANNING}),
    SessionState.PLANNING: frozenset({SessionState.AWAITING_APPROVAL}),
    SessionState.AWAITING_APPROVAL: frozenset(
        {
            SessionState.EXECUTING,
            SessionState.REJECTED,
            SessionState.EXPIRED,
        }
    ),
    SessionState.EXECUTING: frozenset({SessionState.COMPLETED}),
}


def is_allowed_transition(current: SessionState, target: SessionState) -> bool:
    """Return whether the normal lifecycle permits this transition."""
    return target in _TRANSITIONS.get(current, frozenset())


def require_transition(current: SessionState, target: SessionState) -> None:
    """Reject illegal or terminal transitions with a stable domain error."""
    if current.is_terminal:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR,
            f"session is terminal in state {current}",
        )
    if not is_allowed_transition(current, target):
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR,
            f"transition from {current} to {target} is not allowed",
        )
