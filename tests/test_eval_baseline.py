from __future__ import annotations

import json
import sys
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch, raises

from devbrief import cli
from devbrief.application.evaluation import (
    DeterministicEvalService,
    load_eval_dataset,
)
from devbrief.domain.errors import DevBriefError, ErrorCode

ROOT = Path(__file__).parent.parent
DATASET = ROOT / "fixtures/evals/bug-triage-v1.json"
BASELINE = ROOT / "docs/evals/bug-triage-v1-baseline.json"


def test_versioned_baseline_is_redacted_and_reproducible() -> None:
    first = DeterministicEvalService().run(DATASET)
    second = DeterministicEvalService().run(DATASET)

    assert first == second
    assert first.dataset_id == "bug-triage-fake-baseline"
    assert first.dataset_version == "1.0.0"
    assert first.report.sample_count == 2
    assert first.report.candidate_f1 == 1
    assert first.report.evidence_coverage == 1
    serialized = first.model_dump_json()
    assert "Synthetic authentication refresh" not in serialized
    assert "Track remediation" not in serialized


def test_versioned_baseline_matches_committed_aggregate() -> None:
    artifact = DeterministicEvalService().run(DATASET)

    assert json.loads(BASELINE.read_text(encoding="utf-8")) == artifact.model_dump(
        mode="json"
    )


def test_dataset_rejects_missing_fixture(tmp_path: Path) -> None:
    source = json.loads(DATASET.read_text(encoding="utf-8"))
    source["samples"][0]["fixture_path"] = "missing.json"
    invalid = tmp_path / "dataset.json"
    invalid.write_text(json.dumps(source), encoding="utf-8")

    with raises(DevBriefError) as raised:
        DeterministicEvalService().run(invalid)

    assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_dataset_rejects_fixture_path_outside_fixtures_root(tmp_path: Path) -> None:
    source = json.loads(DATASET.read_text(encoding="utf-8"))
    source["samples"][0]["fixture_path"] = str(ROOT / "README.md")
    invalid = tmp_path / "dataset.json"
    invalid.write_text(json.dumps(source), encoding="utf-8")

    with raises(DevBriefError) as raised:
        DeterministicEvalService().run(invalid)

    assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_dataset_loader_rejects_unversioned_payload(tmp_path: Path) -> None:
    invalid = tmp_path / "dataset.json"
    invalid.write_text('{"dataset_id":"invalid"}', encoding="utf-8")

    with raises(DevBriefError) as raised:
        load_eval_dataset(invalid)

    assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_cli_evaluation_writes_and_checks_baseline(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    output = tmp_path / "eval.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devbrief",
            "eval",
            "--dataset",
            str(DATASET),
            "--output",
            str(output),
        ],
    )

    assert cli.main() == 0
    assert capsys.readouterr().out == ""
    assert json.loads(output.read_text(encoding="utf-8"))["report"]["sample_count"] == 2

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devbrief",
            "eval",
            "--dataset",
            str(DATASET),
            "--check",
            str(BASELINE),
        ],
    )
    assert cli.main() == 0
    assert '"candidate_f1":1.0' in capsys.readouterr().out.replace(" ", "")


def test_cli_evaluation_returns_two_for_baseline_mismatch(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    mismatched = tmp_path / "mismatched.json"
    mismatched.write_text(
        json.dumps(
            {
                "dataset_id": "bug-triage-fake-baseline",
                "dataset_version": "1.0.0",
                "report": {
                    "sample_version": "1.0.0",
                    "model_version": "fake-rule-analyzer-1.0.0",
                    "prompt_version": "not-applicable-deterministic-1.0.0",
                    "sample_count": 1,
                    "candidate_precision": 0.0,
                    "candidate_recall": 0.0,
                    "candidate_f1": 0.0,
                    "field_accuracy": {},
                    "evidence_coverage": 0.0,
                    "failure_counts": {},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devbrief",
            "eval",
            "--dataset",
            str(DATASET),
            "--check",
            str(mismatched),
        ],
    )

    assert cli.main() == 2
    assert "does not match" in capsys.readouterr().err
