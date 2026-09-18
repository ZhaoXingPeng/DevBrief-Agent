from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Protocol

from devbrief.adapters.fixture_loader import load_fixture
from devbrief.application.orchestration import BugTriageApplication
from devbrief.domain.benchmark import summarize_latencies
from devbrief.domain.contracts import (
    BenchmarkArtifact,
    BenchmarkBudgetTotals,
    BenchmarkReport,
    ExecutionBudget,
    SessionState,
    TriageRunResult,
)
from devbrief.domain.errors import DevBriefError, ErrorCode

_MAX_ITERATIONS = 1_000
_WORKLOAD_ID = "bug-triage-deterministic"
_WORKLOAD_VERSION = "1.0.0"

Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]


class TriageApplication(Protocol):
    """The no-side-effect application boundary used by benchmark iterations."""

    def run(self, path: Path, *, budget: ExecutionBudget) -> TriageRunResult: ...


@dataclass(frozen=True, slots=True)
class _FixtureMetadata:
    path: Path
    artifact_path: str
    digest: str


@dataclass(frozen=True, slots=True)
class _Measurement:
    elapsed_ms: float
    state: SessionState
    error_code: ErrorCode | None
    budget: ExecutionBudget | None


class DeterministicBenchmarkService:
    """Measure the local fake triage workload without retaining per-run contents."""

    def __init__(
        self,
        *,
        application_factory: Callable[[], TriageApplication] | None = None,
        fixtures_root: Path | None = None,
        monotonic: MonotonicClock | None = None,
        now: Clock | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._application_factory = application_factory or self._new_application
        self._fixtures_root = (fixtures_root or Path("fixtures")).resolve()
        self._monotonic = monotonic or perf_counter

    def run(
        self,
        fixture_path: Path,
        *,
        measurement_iterations: int = 30,
        warmup_iterations: int = 3,
    ) -> BenchmarkArtifact:
        """Run bounded fake iterations and return only redacted aggregate metrics."""
        _validate_iterations(
            measurement_iterations, name="measurement iterations", minimum=1
        )
        _validate_iterations(warmup_iterations, name="warmup iterations", minimum=0)
        fixture = self._validate_fixture(fixture_path)

        for _ in range(warmup_iterations):
            warmup = self._measure_once(fixture.path)
            if warmup.error_code is not None:
                raise DevBriefError(
                    warmup.error_code, "benchmark warmup did not complete"
                )

        measurements = tuple(
            self._measure_once(fixture.path) for _ in range(measurement_iterations)
        )
        state_counts: dict[SessionState, int] = {}
        error_counts: dict[ErrorCode, int] = {}
        successful_budgets: list[ExecutionBudget] = []
        for measurement in measurements:
            state_counts[measurement.state] = state_counts.get(measurement.state, 0) + 1
            if measurement.error_code is None:
                if measurement.budget is None:
                    raise DevBriefError(
                        ErrorCode.INTERNAL_ERROR,
                        "benchmark result has no budget summary",
                    )
                successful_budgets.append(measurement.budget)
            else:
                error_counts[measurement.error_code] = (
                    error_counts.get(measurement.error_code, 0) + 1
                )

        return BenchmarkArtifact(
            workload_id=_WORKLOAD_ID,
            workload_version=_WORKLOAD_VERSION,
            fixture_path=fixture.artifact_path,
            fixture_digest=fixture.digest,
            warmup_iterations=warmup_iterations,
            measurement_iterations=measurement_iterations,
            report=BenchmarkReport(
                latency=summarize_latencies(
                    tuple(item.elapsed_ms for item in measurements)
                ),
                state_counts=dict(
                    sorted(state_counts.items(), key=lambda item: item[0])
                ),
                error_counts=dict(
                    sorted(error_counts.items(), key=lambda item: item[0])
                ),
                budget_totals=_budget_totals(successful_budgets),
            ),
        )

    def _new_application(self) -> BugTriageApplication:
        return BugTriageApplication(now=self._now)

    def _new_budget(self) -> ExecutionBudget:
        return ExecutionBudget(
            max_steps=16,
            max_tool_calls=4,
            deadline_at=self._now() + timedelta(minutes=5),
            max_model_tokens=1_000,
            max_cost=1.0,
        )

    def _validate_fixture(self, path: Path) -> _FixtureMetadata:
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(self._fixtures_root)
        except (OSError, ValueError) as exc:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "benchmark fixture is outside fixtures root",
            ) from exc
        if not resolved.is_file():
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "benchmark fixture is missing"
            )
        fixture = load_fixture(resolved)
        canonical = json.dumps(
            fixture.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return _FixtureMetadata(
            path=resolved,
            artifact_path=relative.as_posix(),
            digest=f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}",
        )

    def _measure_once(self, path: Path) -> _Measurement:
        started = self._monotonic()
        try:
            result = self._application_factory().run(path, budget=self._new_budget())
        except DevBriefError as exc:
            return _Measurement(
                elapsed_ms=self._elapsed_ms(started),
                state=SessionState.FAILED_TERMINAL,
                error_code=exc.code,
                budget=None,
            )
        return _Measurement(
            elapsed_ms=self._elapsed_ms(started),
            state=result.state,
            error_code=None,
            budget=result.budget,
        )

    def _elapsed_ms(self, started: float) -> float:
        elapsed_ms = (self._monotonic() - started) * 1_000
        if not isfinite(elapsed_ms) or elapsed_ms < 0:
            raise DevBriefError(
                ErrorCode.INTERNAL_ERROR, "benchmark monotonic clock is invalid"
            )
        return elapsed_ms


def validate_max_p95_ms(value: object | None) -> float | None:
    """Reject invalid local p95 gates before any workload is constructed."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "maximum p95 must be finite and non-negative"
        )
    numeric_value = float(value)
    if not isfinite(numeric_value) or numeric_value < 0:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "maximum p95 must be finite and non-negative"
        )
    return numeric_value


def p95_exceeds(artifact: BenchmarkArtifact, *, max_p95_ms: float | None) -> bool:
    """Evaluate an optional local gate against the artifact's reported p95."""
    threshold = validate_max_p95_ms(max_p95_ms)
    return threshold is not None and artifact.report.latency.p95_ms > threshold


def _validate_iterations(value: object, *, name: str, minimum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= _MAX_ITERATIONS
    ):
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR,
            f"{name} must be between {minimum} and {_MAX_ITERATIONS}",
        )


def _budget_totals(budgets: list[ExecutionBudget]) -> BenchmarkBudgetTotals:
    return BenchmarkBudgetTotals(
        result_count=len(budgets),
        consumed_steps=sum(item.consumed_steps for item in budgets),
        consumed_tool_calls=sum(item.consumed_tool_calls for item in budgets),
        consumed_model_tokens=sum(item.consumed_model_tokens for item in budgets),
        consumed_cost=sum(item.consumed_cost for item in budgets),
    )
