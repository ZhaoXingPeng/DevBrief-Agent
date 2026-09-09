from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

from pydantic import ValidationError

from devbrief.adapters.fake_analyzer import (
    FakeAnalyzerOutputBuilder,
    FakeFixtureAnalyzerAdapter,
)
from devbrief.domain.contracts import DecisionCandidate
from devbrief.domain.errors import DevBriefError, ErrorCode


class CandidateExtractor(Protocol):
    """Adapter boundary that returns untrusted candidate payloads for validation."""

    def extract(self, path: Path) -> object: ...


class FakeBugTriageAnalyzer:
    """Validate deterministic fake candidates without retaining transcript content."""

    def __init__(
        self,
        *,
        extractor: CandidateExtractor | None = None,
        output_builder: FakeAnalyzerOutputBuilder | None = None,
    ) -> None:
        if extractor is not None and output_builder is not None:
            raise ValueError("provide either extractor or output_builder, not both")
        self._extractor = extractor or FakeFixtureAnalyzerAdapter(output_builder)

    def analyze_path(self, path: Path) -> tuple[DecisionCandidate, ...]:
        """Return schema-validated candidates or a stable validation error."""
        payload = self._extractor.extract(path)
        try:
            return _validate_candidates(payload)
        except (TypeError, ValidationError) as exc:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "invalid fake analyzer output"
            ) from exc


def _validate_candidates(payload: object) -> tuple[DecisionCandidate, ...]:
    if not isinstance(payload, list):
        raise TypeError("fake analyzer output must be a list")
    candidates: list[DecisionCandidate] = []
    for item in cast(list[object], payload):
        candidates.append(DecisionCandidate.model_validate(item))
    return tuple(candidates)
