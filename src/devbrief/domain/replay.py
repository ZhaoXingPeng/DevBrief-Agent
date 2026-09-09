from __future__ import annotations

from collections.abc import Sequence

from devbrief.domain.contracts import (
    SessionState,
    TraceReplayReport,
    TraceSpan,
)


class TraceReplayer:
    """Reconstruct fake execution metadata without replaying side effects."""

    def replay(
        self,
        spans: Sequence[TraceSpan],
        *,
        expected_final_state: SessionState | None = None,
        expected_plan_hash: str | None = None,
        expected_tool_decisions: Sequence[str] | None = None,
        expected_receipt_refs: Sequence[str] | None = None,
    ) -> TraceReplayReport:
        if not spans:
            return TraceReplayReport(replayable=False, mismatches=["trace_empty"])

        first = spans[0]
        mismatches: list[str] = []
        seen_span_ids: set[str] = set()
        state_sequence: list[SessionState] = []
        plan_hashes: list[str] = []
        tool_decisions: list[str] = []
        receipt_refs: list[str] = []
        current_state: SessionState | None = None

        for item in spans:
            if item.span_id in seen_span_ids:
                _add_once(mismatches, "duplicate_span_id")
            seen_span_ids.add(item.span_id)
            if item.trace_id != first.trace_id:
                _add_once(mismatches, "trace_id_mismatch")
            if item.session_id != first.session_id:
                _add_once(mismatches, "session_id_mismatch")
            if item.plan_hash is not None:
                plan_hashes.append(item.plan_hash)
            if item.tool_name is not None:
                decision = item.tool_decision or "unknown"
                tool_decisions.append(f"{item.tool_name}:{decision}")
            if item.receipt_id is not None:
                receipt_refs.append(item.receipt_id)
            if item.state_after is None:
                continue
            if current_state is None:
                if item.state_before is not None:
                    _add_once(mismatches, "initial_state_mismatch")
            elif item.state_before is not current_state:
                _add_once(mismatches, "state_before_mismatch")
            state_sequence.append(item.state_after)
            current_state = item.state_after

        if not state_sequence:
            _add_once(mismatches, "state_missing")
        if (
            expected_final_state is not None
            and current_state is not expected_final_state
        ):
            _add_once(mismatches, "final_state_mismatch")
        if expected_plan_hash is not None and expected_plan_hash not in plan_hashes:
            _add_once(mismatches, "plan_hash_mismatch")
        if (
            expected_tool_decisions is not None
            and list(expected_tool_decisions) != tool_decisions
        ):
            _add_once(mismatches, "tool_decisions_mismatch")
        if (
            expected_receipt_refs is not None
            and list(expected_receipt_refs) != receipt_refs
        ):
            _add_once(mismatches, "receipt_refs_mismatch")

        return TraceReplayReport(
            replayable=not mismatches,
            trace_id=first.trace_id,
            session_id=first.session_id,
            final_state=current_state,
            state_sequence=state_sequence,
            plan_hashes=plan_hashes,
            tool_decisions=tool_decisions,
            receipt_refs=receipt_refs,
            mismatches=mismatches,
        )


def _add_once(mismatches: list[str], code: str) -> None:
    if code not in mismatches:
        mismatches.append(code)
