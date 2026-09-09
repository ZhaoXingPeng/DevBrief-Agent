from __future__ import annotations

import json
from pathlib import Path

import pytest

from devbrief.application.meeting_input import InMemoryMeetingInputService
from devbrief.domain.errors import DevBriefError, ErrorCode


def fixture_payload(text: str = "Synthetic decision text.") -> dict[str, object]:
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
                "text": "Owner and due date remain unknown.",
            },
        ],
    }


def write_fixture(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_import_creates_stable_session_and_evidence_references(tmp_path: Path) -> None:
    service = InMemoryMeetingInputService()
    imported = service.import_path(write_fixture(tmp_path, fixture_payload()))

    assert imported.session_id == "ses_a8b0b6db06274fa0"
    assert imported.trace_id == "trc_ses_a8b0b6db06274fa0"
    assert imported.fixture_id == "bug-triage-redacted-v1"
    assert imported.fixture_version == "1.0.0"
    assert imported.segment_count == 2
    assert [item.reference for item in imported.evidence] == [
        "fixture://bug-triage-redacted-v1/1.0.0#seg_001",
        "fixture://bug-triage-redacted-v1/1.0.0#seg_002",
    ]


def test_repeated_import_is_idempotent_and_conflicting_content_is_rejected(
    tmp_path: Path,
) -> None:
    service = InMemoryMeetingInputService()
    path = write_fixture(tmp_path, fixture_payload())

    first = service.import_path(path)
    second = service.import_path(path)

    assert second == first
    assert len(service.traces.list_for(first.trace_id)) == 1

    path.write_text(
        json.dumps(fixture_payload("Changed synthetic text.")), encoding="utf-8"
    )
    with pytest.raises(DevBriefError) as raised:
        service.import_path(path)

    assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_import_trace_retains_metadata_and_evidence_not_transcript_text(
    tmp_path: Path,
) -> None:
    private_meeting_text = "Private synthetic meeting sentence."
    service = InMemoryMeetingInputService()
    imported = service.import_path(
        write_fixture(tmp_path, fixture_payload(private_meeting_text))
    )

    trace = service.traces.list_for(imported.trace_id)
    summaries = "\n".join(
        f"{span.input_summary}\n{span.output_summary}" for span in trace
    )

    assert "fixture=bug-triage-redacted-v1" in summaries
    assert "segments=2" in summaries
    assert private_meeting_text not in summaries
    assert "Owner and due date remain unknown." not in summaries
    assert "text" not in imported.model_dump()
    assert "owner" not in imported.model_dump()
    assert "due_at" not in imported.model_dump()


def test_invalid_fixture_creates_no_import_or_trace(tmp_path: Path) -> None:
    service = InMemoryMeetingInputService()
    invalid = fixture_payload()
    invalid["redacted"] = False

    with pytest.raises(DevBriefError) as raised:
        service.import_path(write_fixture(tmp_path, invalid))

    assert raised.value.code is ErrorCode.VALIDATION_ERROR
    assert service.get_import("ses_a8b0b6db06274fa0") is None
    assert service.traces.list_for("trc_ses_a8b0b6db06274fa0") == ()
