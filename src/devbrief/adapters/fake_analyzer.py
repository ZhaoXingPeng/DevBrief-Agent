from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

from devbrief.adapters.fixture_loader import load_fixture
from devbrief.domain.contracts import TranscriptFixture, TranscriptSegment

FakeAnalyzerOutputBuilder = Callable[[TranscriptFixture], object]


class FakeFixtureAnalyzerAdapter:
    """Read a redacted fixture transiently and return deterministic fake output."""

    def __init__(self, output_builder: FakeAnalyzerOutputBuilder | None = None) -> None:
        self._output_builder = output_builder or build_bug_triage_output

    def extract(self, path: Path) -> object:
        """Validate and analyze one fixture without retaining its transcript body."""
        return self._output_builder(load_fixture(path))


def build_bug_triage_output(fixture: TranscriptFixture) -> object:
    """Produce narrow, auditable fake output for the synthetic Bug Triage fixture."""
    candidates: list[dict[str, object]] = []
    for segment in fixture.segments:
        if _reports_authentication_refresh_failure(segment):
            evidence = [_transcript_evidence(fixture, segment)]
            candidates.append(
                {
                    "candidate_id": _candidate_id(fixture, segment, "action"),
                    "kind": "action_item",
                    "statement": (
                        "Track remediation for the reported authentication refresh "
                        "failure."
                    ),
                    "owner": "unknown",
                    "due_at": "unknown",
                    "priority": _priority(segment),
                    "acceptance_criteria": [
                        "Confirm a remediation owner.",
                        "Confirm a due date before external planning.",
                    ],
                    "evidence": evidence,
                    "confidence": 1.0,
                    "status": "needs_clarification",
                }
            )
            candidates.append(
                {
                    "candidate_id": _candidate_id(fixture, segment, "clarify"),
                    "kind": "clarification",
                    "statement": (
                        "Clarify the remediation owner and due date before creating "
                        "a task plan."
                    ),
                    "owner": "unknown",
                    "due_at": "unknown",
                    "priority": "unknown",
                    "evidence": evidence,
                    "confidence": 1.0,
                    "status": "needs_clarification",
                }
            )
    return candidates


def _reports_authentication_refresh_failure(segment: TranscriptSegment) -> bool:
    normalized = segment.text.casefold()
    return ("authentication refresh" in normalized or "认证刷新" in normalized) and (
        "failure" in normalized or "故障" in normalized
    )


def _priority(segment: TranscriptSegment) -> str:
    return "p0" if "p0" in segment.text.casefold() else "unknown"


def _transcript_evidence(
    fixture: TranscriptFixture, segment: TranscriptSegment
) -> dict[str, str]:
    return {
        "kind": "transcript_segment",
        "reference": (
            f"fixture://{fixture.fixture_id}/{fixture.fixture_version}"
            f"#{segment.segment_id}"
        ),
    }


def _candidate_id(
    fixture: TranscriptFixture, segment: TranscriptSegment, candidate_kind: str
) -> str:
    identity = (
        f"{fixture.fixture_id}\x00{fixture.fixture_version}\x00"
        f"{segment.segment_id}\x00{candidate_kind}"
    )
    return f"can_{sha256(identity.encode()).hexdigest()[:16]}"
