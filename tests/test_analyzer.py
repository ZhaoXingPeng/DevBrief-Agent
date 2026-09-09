from __future__ import annotations

import json
from pathlib import Path

import pytest

from devbrief.application.analyzer import FakeBugTriageAnalyzer
from devbrief.domain.contracts import (
    CandidateKind,
    CandidateStatus,
    Priority,
    Resolution,
)
from devbrief.domain.errors import DevBriefError, ErrorCode


def fixture_payload(text: str) -> dict[str, object]:
    return {
        "fixture_id": "bug-triage-redacted-v1",
        "fixture_version": "1.0.0",
        "redacted": True,
        "segments": [
            {
                "segment_id": "seg_001",
                "start_ms": 0,
                "end_ms": 2400,
                "speaker": "engineer_a",
                "text": text,
            },
            {
                "segment_id": "seg_002",
                "start_ms": 2600,
                "end_ms": 5100,
                "speaker": "engineer_b",
                "text": "The owner and due date remain unknown until confirmation.",
            },
        ],
    }


def write_fixture(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture_payload(text)), encoding="utf-8")
    return path


def test_fake_analyzer_is_deterministic_and_links_candidates_to_segments(
    tmp_path: Path,
) -> None:
    path = write_fixture(
        tmp_path, "Synthetic authentication refresh failure needs a tracked fix."
    )
    analyzer = FakeBugTriageAnalyzer()

    first = analyzer.analyze_path(path)
    second = analyzer.analyze_path(path)

    assert first == second
    assert first
    action = first[0]
    assert action.kind is CandidateKind.ACTION_ITEM
    assert action.priority is Priority.UNKNOWN
    assert action.owner is Resolution.UNKNOWN
    assert action.due_at is Resolution.UNKNOWN
    assert action.status is CandidateStatus.NEEDS_CLARIFICATION
    assert action.evidence
    assert action.evidence[0].reference == (
        "fixture://bug-triage-redacted-v1/1.0.0#seg_001"
    )
    assert all(item.quote is None for item in action.evidence)


def test_analyzer_preserves_explicit_priority_but_never_infers_owner_or_date(
    tmp_path: Path,
) -> None:
    path = write_fixture(
        tmp_path, "P0 authentication refresh failure: 张三本周五前修复。"
    )

    candidates = FakeBugTriageAnalyzer().analyze_path(path)

    action = candidates[0]
    assert action.priority is Priority.P0
    assert action.owner is Resolution.UNKNOWN
    assert action.due_at is Resolution.UNKNOWN
    assert any(
        item.status is CandidateStatus.NEEDS_CLARIFICATION for item in candidates
    )


def test_analyzer_rejects_malformed_fake_output_as_validation_error(
    tmp_path: Path,
) -> None:
    def malformed(_: object) -> object:
        return [{"candidate_id": "bad", "kind": "not-a-kind"}]

    analyzer = FakeBugTriageAnalyzer(output_builder=malformed)

    with pytest.raises(DevBriefError) as raised:
        analyzer.analyze_path(write_fixture(tmp_path, "A failure needs triage."))

    assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_analyzer_does_not_expose_fixture_text_or_secrets_in_candidate_model(
    tmp_path: Path,
) -> None:
    private_text = "token=private-value; authentication refresh failure needs a fix"
    candidates = FakeBugTriageAnalyzer().analyze_path(
        write_fixture(tmp_path, private_text)
    )

    serialized = json.dumps([item.model_dump(mode="json") for item in candidates])
    assert private_text not in serialized
    assert "private-value" not in serialized
    assert "token=" not in serialized


def test_analyzer_rejects_unredacted_fixture_before_building_output(
    tmp_path: Path,
) -> None:
    payload = fixture_payload("A failure needs triage.")
    payload["redacted"] = False
    path = tmp_path / "unredacted.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DevBriefError) as raised:
        FakeBugTriageAnalyzer().analyze_path(path)

    assert raised.value.code is ErrorCode.VALIDATION_ERROR
