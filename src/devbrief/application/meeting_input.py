from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from devbrief.adapters.fixture_loader import load_fixture
from devbrief.domain.contracts import (
    ImportedTranscript,
    TraceKind,
    TraceSpan,
    TranscriptFixture,
)
from devbrief.domain.errors import DevBriefError, ErrorCode


class InMemoryTraceStore:
    """Append-only, metadata-only trace storage for the local fake input path."""

    def __init__(self) -> None:
        self._items: dict[str, list[TraceSpan]] = {}

    def append(self, span: TraceSpan) -> None:
        self._items.setdefault(span.trace_id, []).append(span)

    def list_for(self, trace_id: str) -> tuple[TraceSpan, ...]:
        return tuple(self._items.get(trace_id, []))


class InMemoryMeetingInputService:
    """Import a validated fixture without retaining transcript text in app state."""

    def __init__(self) -> None:
        self._imports: dict[str, ImportedTranscript] = {}
        self.traces = InMemoryTraceStore()

    def import_path(self, path: Path) -> ImportedTranscript:
        fixture = load_fixture(path)
        imported = self._to_imported_transcript(fixture)
        existing = self._imports.get(imported.session_id)
        if existing is not None:
            if existing.fixture_digest == imported.fixture_digest:
                return existing
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "fixture identity conflicts with an existing transcript digest",
            )

        self._imports[imported.session_id] = imported
        self._record_ingest(imported)
        return imported

    def get_import(self, session_id: str) -> ImportedTranscript | None:
        return self._imports.get(session_id)

    @staticmethod
    def _to_imported_transcript(fixture: TranscriptFixture) -> ImportedTranscript:
        fixture_digest = _fixture_digest(fixture)
        session_key = f"{fixture.fixture_id}\x00{fixture.fixture_version}".encode()
        session_id = f"ses_{sha256(session_key).hexdigest()[:16]}"
        evidence = [
            {
                "reference": (
                    f"fixture://{fixture.fixture_id}/{fixture.fixture_version}"
                    f"#{segment.segment_id}"
                ),
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
            }
            for segment in fixture.segments
        ]
        return ImportedTranscript(
            session_id=session_id,
            trace_id=f"trc_{session_id}",
            fixture_id=fixture.fixture_id,
            fixture_version=fixture.fixture_version,
            fixture_digest=fixture_digest,
            segment_count=len(fixture.segments),
            evidence=evidence,
        )

    def _record_ingest(self, imported: ImportedTranscript) -> None:
        self.traces.append(
            TraceSpan(
                span_id=f"spn_{imported.session_id}_1",
                trace_id=imported.trace_id,
                session_id=imported.session_id,
                kind=TraceKind.INPUT_INGEST,
                input_summary=(
                    f"fixture={imported.fixture_id}; "
                    f"version={imported.fixture_version}; "
                    f"segments={imported.segment_count}; "
                    f"digest={imported.fixture_digest}"
                ),
                output_summary=f"evidence_refs={len(imported.evidence)}",
            )
        )


def _fixture_digest(fixture: TranscriptFixture) -> str:
    canonical = json.dumps(
        fixture.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
