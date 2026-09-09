from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from devbrief.application.analyzer import FakeBugTriageAnalyzer
from devbrief.application.harness import Harness
from devbrief.application.meeting_input import InMemoryMeetingInputService
from devbrief.application.planner import FakeTaskPlanner
from devbrief.domain.contracts import (
    ApprovalStatus,
    ExecutionBudget,
    SessionState,
    TriageRunResult,
)
from devbrief.domain.errors import DevBriefError, ErrorCode

Clock = Callable[[], datetime]


class BugTriageApplication:
    """Run the deterministic Bug Triage workflow up to the approval boundary."""

    def __init__(
        self,
        *,
        now: Clock | None = None,
        input_service: InMemoryMeetingInputService | None = None,
        harness: Harness | None = None,
        analyzer: FakeBugTriageAnalyzer | None = None,
        planner: FakeTaskPlanner | None = None,
    ) -> None:
        self.input_service = input_service or InMemoryMeetingInputService()
        self.harness = harness or Harness(now=now)
        self.analyzer = analyzer or FakeBugTriageAnalyzer()
        self.planner = planner or FakeTaskPlanner()

    def run(self, path: Path, *, budget: ExecutionBudget) -> TriageRunResult:
        """Import, analyze and plan one fixture without executing external tools."""
        imported = self.input_service.import_path(path)
        session = self.harness.create_session(imported.session_id, budget)
        try:
            session = self.harness.transition(
                session.session_id, SessionState.INGESTING
            )
            self.harness.record_ingest(
                session.session_id,
                fixture_id=imported.fixture_id,
                version=imported.fixture_version,
                segments=imported.segment_count,
            )
            session = self.harness.transition(
                session.session_id, SessionState.ANALYZING
            )
            candidates = self.analyzer.analyze_path(path)
            session = self.harness.transition(session.session_id, SessionState.PLANNING)
            draft = self.planner.create_draft(candidates)
            session = self.harness.transition(
                session.session_id, SessionState.AWAITING_APPROVAL
            )
            return TriageRunResult(
                session_id=session.session_id,
                trace_id=session.trace_id,
                state=session.state,
                candidates=list(candidates),
                draft=draft,
                approval_status=ApprovalStatus.PENDING,
                budget=session.budget,
            )
        except DevBriefError as exc:
            self.harness.fail_terminal(session.session_id, exc.code)
            raise
        except (TypeError, ValueError) as exc:
            self.harness.fail_terminal(session.session_id, ErrorCode.INTERNAL_ERROR)
            raise DevBriefError(
                ErrorCode.INTERNAL_ERROR, "triage application failed"
            ) from exc
