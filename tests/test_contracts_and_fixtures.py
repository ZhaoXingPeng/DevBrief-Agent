from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from devbrief.adapters.fixture_loader import load_fixture
from devbrief.domain.contracts import (
    DecisionCandidate,
    Evidence,
    EvidenceKind,
    ExecutionBudget,
    Priority,
    Resolution,
    TranscriptFixture,
)
from devbrief.domain.errors import DevBriefError, ErrorCode


def valid_fixture() -> dict[str, object]:
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
                "text": "Synthetic fixture text.",
            }
        ],
    }


def test_fixture_requires_version_redaction_and_timestamp_fields() -> None:
    missing_version = valid_fixture()
    del missing_version["fixture_version"]
    missing_timestamp = valid_fixture()
    del missing_timestamp["segments"][0]["start_ms"]  # type: ignore[index]
    missing_redaction = valid_fixture()
    del missing_redaction["redacted"]

    for invalid in (missing_version, missing_timestamp, missing_redaction):
        with pytest.raises(ValidationError):
            TranscriptFixture.model_validate(invalid)


def test_fixture_rejects_unredacted_invalid_time_and_non_deterministic_order() -> None:
    unredacted = valid_fixture()
    unredacted["redacted"] = False
    invalid_time = valid_fixture()
    invalid_time["segments"][0]["end_ms"] = 0  # type: ignore[index]
    unordered = valid_fixture()
    unordered["segments"] = [  # type: ignore[index]
        {**valid_fixture()["segments"][0], "segment_id": "seg_b"},  # type: ignore[index]
        {**valid_fixture()["segments"][0], "segment_id": "seg_a"},  # type: ignore[index]
    ]

    for invalid in (unredacted, invalid_time, unordered):
        with pytest.raises(ValidationError):
            TranscriptFixture.model_validate(invalid)


def test_loader_returns_stable_validation_error(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps({"fixture_id": "missing-fields"}), encoding="utf-8"
    )

    with pytest.raises(DevBriefError) as raised:
        load_fixture(fixture_path)

    assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_valid_fixture_can_be_loaded_and_is_versioned(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(valid_fixture()), encoding="utf-8")

    fixture = load_fixture(fixture_path)

    assert fixture.fixture_version == "1.0.0"
    assert fixture.segments[0].segment_id == "seg_001"


def test_decision_candidate_requires_evidence_and_preserves_unknown_fields() -> None:
    payload: dict[str, object] = {
        "candidate_id": "can_01",
        "kind": "action_item",
        "statement": "Resolve the synthetic failure.",
        "owner": Resolution.UNKNOWN,
        "due_at": Resolution.AMBIGUOUS,
        "priority": Priority.P0,
        "evidence": [],
        "confidence": 0.8,
    }
    with pytest.raises(ValidationError):
        DecisionCandidate.model_validate(payload)

    candidate = DecisionCandidate.model_validate(
        {
            **payload,
            "evidence": [
                Evidence(kind=EvidenceKind.TRANSCRIPT_SEGMENT, reference="seg_001")
            ],
        }
    )
    assert candidate.owner is Resolution.UNKNOWN
    assert candidate.due_at is Resolution.AMBIGUOUS


def test_execution_budget_is_fully_bound_at_session_start() -> None:
    budget = ExecutionBudget(
        max_steps=3,
        max_tool_calls=2,
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        max_model_tokens=100,
        max_cost=1.5,
    )
    assert budget.consumed_steps == 0
