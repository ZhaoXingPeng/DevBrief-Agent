from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn, cast

from pydantic import ValidationError
from pytest import CaptureFixture, MonkeyPatch, raises

from devbrief import cli
from devbrief.application.safety_evaluation import (
    DeterministicSafetyEvalService,
    load_safety_eval_dataset,
)
from devbrief.domain.contracts import SafetyEvalArtifact
from devbrief.domain.errors import DevBriefError, ErrorCode

ROOT = Path(__file__).parent.parent
DATASET = ROOT / "fixtures/evals/harness-safety-v1.json"
BASELINE = ROOT / "docs/evals/harness-safety-v1-baseline.json"


def test_safety_eval_is_reproducible_redacted_and_covers_harness_boundaries() -> None:
    first = DeterministicSafetyEvalService().run(DATASET)
    second = DeterministicSafetyEvalService().run(DATASET)

    assert first == second
    assert first.dataset_id == "harness-safety-fake"
    assert first.dataset_version == "1.0.0"
    assert first.runner_version == "safety-harness-runner-1.0.0"
    assert first.report.scenario_version == "1.0.0"
    assert first.report.scenario_count == 8
    assert first.report.passed_count == 8
    assert first.report.failed_count == 0
    assert first.report.failure_counts == {}
    assert {item.scenario_id for item in first.report.results} == {
        "external_write_missing_approval",
        "external_write_expired_approval",
        "external_write_scope_mismatch",
        "external_write_plan_hash_tamper",
        "external_write_budget_exhausted",
        "idempotency_replay",
        "unknown_outcome_query_first",
        "trace_integrity_recovery_denied",
    }
    assert all(item.passed for item in first.report.results)
    serialized = first.model_dump_json()
    assert "private-safety-plan-body" not in serialized
    assert "provider-private-id" not in serialized
    assert "https://fake.invalid" not in serialized


def test_safety_eval_reports_stable_mismatch_codes_for_changed_expectations(
    tmp_path: Path,
) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["scenarios"][0]["expected"]["provider_create_calls"] = 1
    changed = tmp_path / "changed-safety.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    artifact = DeterministicSafetyEvalService().run(changed)

    assert artifact.report.scenario_count == 8
    assert artifact.report.passed_count == 7
    assert artifact.report.failed_count == 1
    assert artifact.report.failure_counts == {"provider_create_calls_mismatch": 1}
    failed = next(item for item in artifact.report.results if not item.passed)
    assert failed.scenario_id == "external_write_missing_approval"
    assert failed.observed_error_code is ErrorCode.APPROVAL_REQUIRED
    assert failed.mismatch_codes == ["provider_create_calls_mismatch"]


def test_safety_artifact_rejects_unknown_mismatch_codes() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    result = payload["report"]["results"][0]
    result["passed"] = False
    result["mismatch_codes"] = ["unregistered_mismatch"]
    payload["report"]["passed_count"] = 7
    payload["report"]["failed_count"] = 1
    payload["report"]["failure_counts"] = {"unregistered_mismatch": 1}

    with raises(ValidationError):
        SafetyEvalArtifact.model_validate(payload)


def test_safety_dataset_rejects_unknown_kind_duplicate_ids_and_mixed_versions(
    tmp_path: Path,
) -> None:
    for name, mutate in (
        ("unknown-kind", _set_unknown_kind),
        ("duplicate-id", _set_duplicate_id),
        ("mixed-version", _set_mixed_version),
    ):
        current = _dataset_payload()
        mutate(_scenario_payloads(current))
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(current), encoding="utf-8")

        with raises(DevBriefError) as raised:
            load_safety_eval_dataset(path)
        assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_safety_dataset_rejects_extra_fields_before_scenario_execution(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    payload = _dataset_payload()
    _scenario_payloads(payload)[0]["provider_url"] = "https://unsafe.invalid"
    path = tmp_path / "extra-field.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        DeterministicSafetyEvalService,
        "_execute",
        _unexpected_scenario_execution,
    )

    with raises(DevBriefError) as raised:
        DeterministicSafetyEvalService().run(path)

    assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_committed_safety_baseline_matches_complete_aggregate() -> None:
    artifact = DeterministicSafetyEvalService().run(DATASET)

    assert json.loads(BASELINE.read_text(encoding="utf-8")) == artifact.model_dump(
        mode="json"
    )


def test_cli_safety_eval_writes_and_checks_baseline(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    output = tmp_path / "safety-eval.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devbrief",
            "safety-eval",
            "--dataset",
            str(DATASET),
            "--output",
            str(output),
        ],
    )

    assert cli.main() == 0
    assert capsys.readouterr().out == ""
    assert (
        json.loads(output.read_text(encoding="utf-8"))["report"]["scenario_count"] == 8
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devbrief",
            "safety-eval",
            "--dataset",
            str(DATASET),
            "--check",
            str(BASELINE),
        ],
    )
    assert cli.main() == 0
    assert '"passed_count":8' in capsys.readouterr().out.replace(" ", "")


def test_cli_safety_eval_returns_two_for_valid_baseline_mismatch(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    mismatched_payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    mismatched_payload["dataset_version"] = "9.9.9"
    mismatched = tmp_path / "mismatched-safety-baseline.json"
    mismatched.write_text(json.dumps(mismatched_payload), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devbrief",
            "safety-eval",
            "--dataset",
            str(DATASET),
            "--check",
            str(mismatched),
        ],
    )

    assert cli.main() == 2
    assert "does not match" in capsys.readouterr().err


def _dataset_payload() -> dict[str, object]:
    return cast(dict[str, object], json.loads(DATASET.read_text(encoding="utf-8")))


def _scenario_payloads(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], payload["scenarios"])


def _set_unknown_kind(scenarios: list[dict[str, object]]) -> None:
    scenarios[0]["kind"] = "arbitrary_python"


def _set_duplicate_id(scenarios: list[dict[str, object]]) -> None:
    scenarios[1]["scenario_id"] = scenarios[0]["scenario_id"]


def _set_mixed_version(scenarios: list[dict[str, object]]) -> None:
    scenarios[1]["scenario_version"] = "2.0.0"


def _unexpected_scenario_execution(*_: object) -> NoReturn:
    raise AssertionError("invalid safety datasets must not execute scenarios")
