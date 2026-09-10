from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from devbrief.application.analyzer import FakeBugTriageAnalyzer
from devbrief.domain.contracts import (
    EvalArtifact,
    EvalDataset,
    EvalSample,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.eval import EvalRunner


class DeterministicEvalService:
    """Evaluate labeled public fixtures without retaining source text in reports."""

    def __init__(
        self,
        *,
        analyzer: FakeBugTriageAnalyzer | None = None,
        runner: EvalRunner | None = None,
    ) -> None:
        self._analyzer = analyzer or FakeBugTriageAnalyzer()
        self._runner = runner or EvalRunner()

    def run(self, dataset_path: Path) -> EvalArtifact:
        dataset = load_eval_dataset(dataset_path)
        samples = tuple(
            EvalSample(
                sample_id=sample.sample_id,
                sample_version=dataset.sample_version,
                expected=sample.expected,
                predicted=list(
                    self._analyzer.analyze_path(
                        _fixture_path(dataset_path, sample.fixture_path)
                    )
                ),
            )
            for sample in dataset.samples
        )
        report = self._runner.run(
            samples,
            model_version=dataset.model_version,
            prompt_version=dataset.prompt_version,
        )
        return EvalArtifact(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            report=report,
        )


def load_eval_dataset(path: Path) -> EvalDataset:
    """Load a versioned label set and surface stable validation failures."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EvalDataset.model_validate(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "invalid evaluation dataset"
        ) from exc


def load_eval_artifact(path: Path) -> EvalArtifact:
    """Load a committed baseline report without accepting arbitrary JSON."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EvalArtifact.model_validate(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "invalid evaluation baseline"
        ) from exc


def _fixture_path(dataset_path: Path, fixture_path: str) -> Path:
    relative_path = Path(fixture_path)
    fixtures_root = dataset_path.parent.parent.resolve()
    if relative_path.is_absolute():
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "evaluation fixture path must be relative"
        )
    path = (dataset_path.parent / relative_path).resolve()
    try:
        path.relative_to(fixtures_root)
    except ValueError as exc:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "evaluation fixture is outside fixtures root"
        ) from exc
    if not path.is_file():
        raise DevBriefError(ErrorCode.VALIDATION_ERROR, "evaluation fixture is missing")
    return path
