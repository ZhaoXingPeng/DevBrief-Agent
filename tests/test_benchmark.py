from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from math import inf
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch, raises

from devbrief import cli
from devbrief.application.benchmark import (
    DeterministicBenchmarkService,
    TriageApplication,
    validate_max_p95_ms,
)
from devbrief.domain.benchmark import nearest_rank_percentile
from devbrief.domain.contracts import (
    BenchmarkArtifact,
    BenchmarkBudgetTotals,
    BenchmarkLatencySummary,
    BenchmarkReport,
    ExecutionBudget,
    SessionState,
    TriageRunResult,
)
from devbrief.domain.errors import DevBriefError, ErrorCode

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "fixtures"
FIXTURE = FIXTURES / "transcripts/bug-triage-redacted-v1.json"
RUN_AT = datetime(2026, 9, 18, 0, 0, tzinfo=UTC)


def _monotonic(values: list[float]) -> Callable[[], float]:
    iterator = iter(values)
    return lambda: next(iterator)


def _artifact(*, p95_ms: float) -> BenchmarkArtifact:
    return BenchmarkArtifact(
        schema_version=1,
        workload_id="bug-triage-deterministic",
        workload_version="1.0.0",
        fixture_path="transcripts/bug-triage-redacted-v1.json",
        fixture_digest="sha256:" + "0" * 64,
        warmup_iterations=0,
        measurement_iterations=1,
        report=BenchmarkReport(
            latency=BenchmarkLatencySummary(
                sample_count=1,
                min_ms=p95_ms,
                p50_ms=p95_ms,
                p95_ms=p95_ms,
                max_ms=p95_ms,
            ),
            state_counts={SessionState.AWAITING_APPROVAL: 1},
            error_counts={},
            budget_totals=BenchmarkBudgetTotals(
                result_count=1,
                consumed_steps=4,
                consumed_tool_calls=0,
                consumed_model_tokens=0,
                consumed_cost=0,
            ),
        ),
    )


def test_benchmark_reports_redacted_nearest_rank_metrics_and_budget_totals() -> None:
    service = DeterministicBenchmarkService(
        fixtures_root=FIXTURES,
        monotonic=_monotonic(
            [
                0.0,
                1.0,
                1.0,
                2.0,
                10.0,
                13.0,
                20.0,
                25.0,
                30.0,
                50.0,
            ]
        ),
        now=lambda: RUN_AT,
    )

    artifact = service.run(
        FIXTURE,
        measurement_iterations=3,
        warmup_iterations=2,
    )

    assert artifact.schema_version == 1
    assert artifact.workload_id == "bug-triage-deterministic"
    assert artifact.workload_version == "1.0.0"
    assert artifact.fixture_path == "transcripts/bug-triage-redacted-v1.json"
    assert artifact.fixture_digest.startswith("sha256:")
    assert artifact.warmup_iterations == 2
    assert artifact.measurement_iterations == 3
    assert artifact.report.latency.model_dump() == {
        "sample_count": 3,
        "min_ms": 3000.0,
        "p50_ms": 5000.0,
        "p95_ms": 20000.0,
        "max_ms": 20000.0,
    }
    assert artifact.report.state_counts == {SessionState.AWAITING_APPROVAL: 3}
    assert artifact.report.error_counts == {}
    assert artifact.report.budget_totals.model_dump() == {
        "result_count": 3,
        "consumed_steps": 12,
        "consumed_tool_calls": 0,
        "consumed_model_tokens": 0,
        "consumed_cost": 0.0,
    }
    serialized = artifact.model_dump_json()
    assert "Synthetic authentication refresh" not in serialized
    assert "Track remediation" not in serialized


def test_nearest_rank_percentile_and_latency_contract_reject_invalid_ordering() -> None:
    assert nearest_rank_percentile([3.0, 5.0, 20.0], percentile=0.5) == 5.0
    assert nearest_rank_percentile([3.0, 5.0, 20.0], percentile=0.95) == 20.0

    with raises(ValueError):
        BenchmarkLatencySummary(
            sample_count=3,
            min_ms=3,
            p50_ms=20,
            p95_ms=5,
            max_ms=20,
        )

    with raises(ValueError):
        BenchmarkReport(
            latency=BenchmarkLatencySummary(
                sample_count=1,
                min_ms=1,
                p50_ms=1,
                p95_ms=1,
                max_ms=1,
            ),
            state_counts={SessionState.AWAITING_APPROVAL: 1},
            error_counts={ErrorCode.BUDGET_EXHAUSTED: 1},
            budget_totals=BenchmarkBudgetTotals(
                result_count=0,
                consumed_steps=0,
                consumed_tool_calls=0,
                consumed_model_tokens=0,
                consumed_cost=0,
            ),
        )

    with raises(ValueError):
        BenchmarkLatencySummary(
            sample_count=1,
            min_ms=0,
            p50_ms=0,
            p95_ms=inf,
            max_ms=inf,
        )
    with raises(ValueError):
        BenchmarkBudgetTotals(
            result_count=1,
            consumed_steps=0,
            consumed_tool_calls=0,
            consumed_model_tokens=0,
            consumed_cost=inf,
        )


class _FailingApplication:
    def run(self, path: Path, *, budget: ExecutionBudget) -> TriageRunResult:
        raise DevBriefError(ErrorCode.BUDGET_EXHAUSTED, "configured fake failure")


def test_benchmark_aggregates_expected_failures_without_source_text() -> None:
    factory: Callable[[], TriageApplication] = _FailingApplication
    service = DeterministicBenchmarkService(
        application_factory=factory,
        fixtures_root=FIXTURES,
        monotonic=_monotonic([0.0, 4.0, 10.0, 20.0]),
        now=lambda: RUN_AT,
    )

    artifact = service.run(
        FIXTURE,
        measurement_iterations=2,
        warmup_iterations=0,
    )

    assert artifact.report.latency.p50_ms == 4000.0
    assert artifact.report.latency.p95_ms == 10000.0
    assert artifact.report.state_counts == {SessionState.FAILED_TERMINAL: 2}
    assert artifact.report.error_counts == {ErrorCode.BUDGET_EXHAUSTED: 2}
    assert "configured fake failure" not in artifact.model_dump_json()


def test_benchmark_rejects_unsafe_inputs_before_creating_an_application(
    tmp_path: Path,
) -> None:
    def fail_if_called() -> TriageApplication:
        raise AssertionError("application must not be created for invalid input")

    service = DeterministicBenchmarkService(
        application_factory=fail_if_called,
        fixtures_root=FIXTURES,
        now=lambda: RUN_AT,
    )

    for kwargs in (
        {"measurement_iterations": 0, "warmup_iterations": 0},
        {"measurement_iterations": 1, "warmup_iterations": -1},
    ):
        with raises(DevBriefError) as raised:
            service.run(FIXTURE, **kwargs)
        assert raised.value.code is ErrorCode.VALIDATION_ERROR

    with raises(DevBriefError) as invalid_gate:
        validate_max_p95_ms(-1)
    assert invalid_gate.value.code is ErrorCode.VALIDATION_ERROR

    with raises(DevBriefError) as outside:
        service.run(ROOT / "README.md", measurement_iterations=1, warmup_iterations=0)
    assert outside.value.code is ErrorCode.VALIDATION_ERROR

    unredacted_payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    unredacted_payload["redacted"] = False
    unredacted = tmp_path / "unredacted.json"
    unredacted.write_text(json.dumps(unredacted_payload), encoding="utf-8")
    unredacted_service = DeterministicBenchmarkService(
        application_factory=fail_if_called,
        fixtures_root=tmp_path,
        now=lambda: RUN_AT,
    )
    with raises(DevBriefError) as private_input:
        unredacted_service.run(
            unredacted,
            measurement_iterations=1,
            warmup_iterations=0,
        )
    assert private_input.value.code is ErrorCode.VALIDATION_ERROR


def test_cli_benchmark_writes_artifact_before_failing_p95_gate(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    artifact = _artifact(p95_ms=12.0)

    class _FixedBenchmarkService:
        def __init__(self, **_: object) -> None:
            pass

        def run(self, *_: object, **__: object) -> BenchmarkArtifact:
            return artifact

    output = tmp_path / "benchmark.json"
    monkeypatch.setattr(
        cli,
        "DeterministicBenchmarkService",
        _FixedBenchmarkService,
        raising=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devbrief",
            "benchmark",
            "--iterations",
            "1",
            "--warmup",
            "0",
            "--max-p95-ms",
            "10",
            "--output",
            str(output),
        ],
    )

    assert cli.main() == 3
    assert (
        json.loads(output.read_text(encoding="utf-8"))["report"]["latency"]["p95_ms"]
        == 12.0
    )
    assert "p95" in capsys.readouterr().err


def test_cli_benchmark_rejects_invalid_gate_before_constructing_runner(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    class _UnexpectedBenchmarkService:
        def __init__(self, **_: object) -> None:
            raise AssertionError("invalid p95 gate must not construct a runner")

    monkeypatch.setattr(
        cli,
        "DeterministicBenchmarkService",
        _UnexpectedBenchmarkService,
        raising=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devbrief", "benchmark", "--max-p95-ms", "-1"],
    )

    assert cli.main() == 2
    assert "maximum p95" in capsys.readouterr().err
