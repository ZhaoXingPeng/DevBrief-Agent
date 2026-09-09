from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from devbrief.domain.budget import consume_model_usage, consume_step
from devbrief.domain.contracts import (
    Checkpoint,
    ExecutionBudget,
    SessionState,
    TraceKind,
    TraceSpan,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.state_machine import require_transition
from devbrief.domain.trace import redact_summary

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class Session:
    """Runtime-owned session state; only Harness commands can replace it."""

    session_id: str
    trace_id: str
    state: SessionState
    budget: ExecutionBudget


class InMemoryCheckpointRepository:
    """Deterministic checkpoint storage for the no-credential fake runtime."""

    def __init__(self) -> None:
        self._items: dict[str, list[Checkpoint]] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        self._items.setdefault(checkpoint.session_id, []).append(checkpoint)

    def load_latest(self, session_id: str) -> Checkpoint | None:
        checkpoints = self._items.get(session_id, [])
        return checkpoints[-1] if checkpoints else None


class InMemoryTraceStore:
    """Append-only, redacted trace storage for the fake runtime."""

    def __init__(self) -> None:
        self._items: dict[str, list[TraceSpan]] = {}

    def append(self, span: TraceSpan) -> None:
        self._items.setdefault(span.trace_id, []).append(span)

    def list_for(self, trace_id: str) -> tuple[TraceSpan, ...]:
        return tuple(self._items.get(trace_id, []))


class Harness:
    """Deterministic single-Agent lifecycle, budgets, cancellation, and recovery."""

    def __init__(self, *, now: Clock | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._sessions: dict[str, Session] = {}
        self.checkpoints = InMemoryCheckpointRepository()
        self.traces = InMemoryTraceStore()
        self._span_counter = 0
        self._checkpoint_counter = 0

    def create_session(self, session_id: str, budget: ExecutionBudget) -> Session:
        """Create a session with an immutable starting budget and safe checkpoint."""
        if session_id in self._sessions:
            raise DevBriefError(ErrorCode.VALIDATION_ERROR, "session id already exists")
        session = Session(
            session_id=session_id,
            trace_id=f"trc_{session_id}",
            state=SessionState.CREATED,
            budget=budget,
        )
        self._sessions[session_id] = session
        self._trace_state(session, None, SessionState.CREATED, output="session created")
        self._write_checkpoint(session)
        return session

    def get_session(self, session_id: str) -> Session:
        """Load a known session or return a stable validation error."""
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "session does not exist"
            ) from exc

    def transition(self, session_id: str, target: SessionState) -> Session:
        """Apply a legal lifecycle transition and checkpoint the new safe state."""
        session = self.get_session(session_id)
        require_transition(session.state, target)
        try:
            updated_budget = consume_step(session.budget, self._now())
        except DevBriefError as exc:
            self._stop_for(session, exc)
            raise
        updated = replace(session, state=target, budget=updated_budget)
        self._sessions[session_id] = updated
        self._trace_state(updated, session.state, target, output="state changed")
        self._write_checkpoint(updated)
        return updated

    def record_ingest(
        self,
        session_id: str,
        *,
        fixture_id: str,
        version: str,
        segments: int,
    ) -> None:
        """Record fixture metadata only; transcript text never enters this trace."""
        session = self.get_session(session_id)
        if segments < 1:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "fixture must have segments"
            )
        self._append_trace(
            session,
            TraceKind.INPUT_INGEST,
            input_summary=(
                f"fixture={fixture_id}; version={version}; segments={segments}"
            ),
            output_summary="fixture metadata accepted",
        )

    def record_model_usage(
        self, session_id: str, *, tokens: int, cost: float
    ) -> Session:
        """Record local fake model usage against the session's fixed budget."""
        session = self.get_session(session_id)
        if session.state.is_terminal:
            raise DevBriefError(ErrorCode.VALIDATION_ERROR, "session is terminal")
        try:
            updated_budget = consume_model_usage(
                session.budget,
                self._now(),
                tokens=tokens,
                cost=cost,
            )
        except DevBriefError as exc:
            self._stop_for(session, exc)
            raise
        updated = replace(session, budget=updated_budget)
        self._sessions[session_id] = updated
        self._append_trace(
            updated,
            TraceKind.MODEL_CALL,
            input_summary=f"tokens={tokens}; cost={cost:.4f}",
            output_summary="model budget recorded",
        )
        self._write_checkpoint(updated)
        return updated

    def cancel(self, session_id: str) -> Session:
        """Cancel a nonterminal session without discarding its last safe checkpoint."""
        session = self.get_session(session_id)
        if session.state.is_terminal:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "session is already terminal"
            )
        updated = replace(session, state=SessionState.CANCELLED)
        self._sessions[session_id] = updated
        self._trace_state(
            updated,
            session.state,
            SessionState.CANCELLED,
            output="session cancelled",
            error_code=ErrorCode.CANCELLED,
        )
        return updated

    def fail_recoverable(self, session_id: str, reason: str) -> Session:
        """Record a recoverable failure and retain the preceding safe checkpoint."""
        session = self.get_session(session_id)
        if session.state.is_terminal:
            raise DevBriefError(ErrorCode.VALIDATION_ERROR, "session is terminal")
        updated = replace(session, state=SessionState.FAILED_RECOVERABLE)
        self._sessions[session_id] = updated
        self._trace_state(
            updated,
            session.state,
            SessionState.FAILED_RECOVERABLE,
            output="recoverable failure",
            error_code=reason,
        )
        return updated

    def resume(self, session_id: str) -> Session:
        """Resume only a recoverable failure from its last persisted safe checkpoint."""
        session = self.get_session(session_id)
        if session.state is not SessionState.FAILED_RECOVERABLE:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "only recoverable failures can be resumed",
            )
        checkpoint = self.checkpoints.load_latest(session_id)
        if checkpoint is None:
            raise DevBriefError(
                ErrorCode.CHECKPOINT_UNAVAILABLE, "no checkpoint available"
            )
        resumed = replace(
            session,
            state=checkpoint.state,
            budget=checkpoint.budget_summary,
        )
        self._sessions[session_id] = resumed
        self._trace_state(
            resumed,
            SessionState.FAILED_RECOVERABLE,
            checkpoint.state,
            output="resumed from safe checkpoint",
        )
        self._write_checkpoint(resumed)
        return resumed

    def _stop_for(self, session: Session, error: DevBriefError) -> None:
        target = (
            SessionState.FAILED_RECOVERABLE
            if error.code is ErrorCode.DEADLINE_EXCEEDED
            else SessionState.FAILED_TERMINAL
        )
        stopped = replace(session, state=target)
        self._sessions[session.session_id] = stopped
        self._trace_state(
            stopped,
            session.state,
            target,
            output="execution stopped",
            error_code=error.code,
        )

    def _write_checkpoint(self, session: Session) -> None:
        self._checkpoint_counter += 1
        checkpoint = Checkpoint(
            checkpoint_id=f"chk_{session.session_id}_{self._checkpoint_counter}",
            session_id=session.session_id,
            state=session.state,
            budget_summary=session.budget,
            last_event_id=f"spn_{session.session_id}_{self._span_counter}",
            created_at=self._now(),
        )
        self.checkpoints.save(checkpoint)
        self._append_trace(
            session,
            TraceKind.CHECKPOINT,
            input_summary="checkpoint metadata",
            output_summary=f"checkpoint={checkpoint.checkpoint_id}",
        )

    def _trace_state(
        self,
        session: Session,
        before: SessionState | None,
        after: SessionState,
        *,
        output: str,
        error_code: ErrorCode | str | None = None,
    ) -> None:
        error = error_code.value if isinstance(error_code, ErrorCode) else error_code
        self._append_trace(
            session,
            TraceKind.STATE_TRANSITION,
            input_summary="state transition",
            output_summary=output,
            error_code=redact_summary(error) if error is not None else None,
            state_before=before,
            state_after=after,
        )

    def _append_trace(
        self,
        session: Session,
        kind: TraceKind,
        *,
        input_summary: str,
        output_summary: str,
        error_code: str | None = None,
        state_before: SessionState | None = None,
        state_after: SessionState | None = None,
    ) -> None:
        self._span_counter += 1
        self.traces.append(
            TraceSpan(
                span_id=f"spn_{session.session_id}_{self._span_counter}",
                trace_id=session.trace_id,
                session_id=session.session_id,
                kind=kind,
                input_summary=redact_summary(input_summary),
                output_summary=redact_summary(output_summary),
                error_code=error_code,
                state_before=state_before,
                state_after=state_after,
            )
        )
