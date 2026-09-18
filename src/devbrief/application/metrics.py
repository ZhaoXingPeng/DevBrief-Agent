from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from math import isfinite
from pathlib import Path
from re import compile
from typing import TypeVar

from pydantic import ValidationError

from devbrief.domain.contracts import (
    ExecutionBudget,
    RuntimeMetricsBudgetTotals,
    RuntimeMetricsSnapshot,
    SessionState,
    TraceIntegrityReport,
    TraceIntegrityStatus,
    TraceKind,
    TraceSpan,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.trace_integrity import verify_trace_integrity
from devbrief.integration.storage import SQLiteRunStore, StoredRunRecord

_SAFE_TOOL_NAME = compile(r"^[a-z][a-z0-9_-]{0,63}$")


class RuntimeMetricsService:
    """Aggregate only safe counters from a persisted SQLite run store."""

    def collect(self, database: Path) -> RuntimeMetricsSnapshot:
        """Read an existing database without migrations or partial output."""
        if not database.is_file():
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "runtime metrics database does not exist",
            )
        try:
            records = SQLiteRunStore(database).list_records_for_metrics()
            return self._aggregate(records)
        except (sqlite3.Error, OSError, TypeError, ValueError, ValidationError) as exc:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "runtime metrics database is invalid",
            ) from exc

    def _aggregate(self, records: Iterable[StoredRunRecord]) -> RuntimeMetricsSnapshot:
        state_counts: dict[SessionState, int] = {}
        error_counts: dict[ErrorCode, int] = {}
        trace_kind_counts: dict[TraceKind, int] = {}
        tool_counts: dict[str, int] = {}
        integrity_counts: dict[TraceIntegrityStatus, int] = {}
        mismatch_counts: dict[str, int] = {}
        budgets: list[ExecutionBudget] = []
        checkpoint_count = 0
        recover_count = 0
        session_count = 0

        for record in records:
            session_count += 1
            _increment(state_counts, record.result.state)
            budget = _budget_for(record)
            _validate_budget(budget)
            budgets.append(budget)
            for checkpoint in record.checkpoints:
                if checkpoint.schema_version != 1:
                    raise ValueError("stored checkpoint schema is unsupported")
            checkpoint_count += len(record.checkpoints)

            report = _integrity_report(record)
            _increment(integrity_counts, report.status)
            for mismatch in report.mismatches:
                _increment(mismatch_counts, mismatch)

            reported_errors: set[ErrorCode] = set()
            for span in record.traces:
                _validate_tool_name(span)
                _increment(trace_kind_counts, span.kind)
                if span.kind is TraceKind.TOOL_CALL:
                    if span.tool_name is not None:
                        _increment(tool_counts, span.tool_name)
                if span.kind is TraceKind.STATE_TRANSITION and (
                    span.state_before is SessionState.FAILED_RECOVERABLE
                    and span.state_after is not SessionState.FAILED_RECOVERABLE
                ):
                    recover_count += 1
                error_code = _known_error_code(span.error_code)
                if error_code is not None:
                    _increment(error_counts, error_code)
                    reported_errors.add(error_code)

            result_error = _known_error_code(record.result.error)
            if result_error is not None and result_error not in reported_errors:
                _increment(error_counts, result_error)

        return RuntimeMetricsSnapshot(
            session_count=session_count,
            state_counts=_sorted_enum_counts(state_counts),
            error_counts=_sorted_enum_counts(error_counts),
            trace_kind_counts=_sorted_enum_counts(trace_kind_counts),
            tool_counts=_sorted_string_counts(tool_counts),
            span_count=sum(trace_kind_counts.values()),
            model_call_count=trace_kind_counts.get(TraceKind.MODEL_CALL, 0),
            tool_call_count=trace_kind_counts.get(TraceKind.TOOL_CALL, 0),
            checkpoint_count=checkpoint_count,
            recover_count=recover_count,
            trace_integrity_counts=_sorted_enum_counts(integrity_counts),
            trace_integrity_mismatch_counts=_sorted_string_counts(mismatch_counts),
            budget_totals=RuntimeMetricsBudgetTotals(
                session_count=session_count,
                consumed_steps=sum(item.consumed_steps for item in budgets),
                consumed_tool_calls=sum(item.consumed_tool_calls for item in budgets),
                consumed_model_tokens=sum(
                    item.consumed_model_tokens for item in budgets
                ),
                consumed_cost=sum(item.consumed_cost for item in budgets),
            ),
        )


def _budget_for(record: StoredRunRecord) -> ExecutionBudget:
    if record.checkpoints:
        return record.checkpoints[-1].budget_summary
    return record.result.budget


def _validate_budget(budget: ExecutionBudget) -> None:
    if not isfinite(budget.consumed_cost):
        raise ValueError("stored budget cost is not finite")


def _integrity_report(record: StoredRunRecord) -> TraceIntegrityReport:
    report = verify_trace_integrity(record.traces, checkpoints=record.checkpoints)
    mismatches = list(report.mismatches)
    if any(
        span.session_id != record.result.session_id
        or span.trace_id != record.result.trace_id
        for span in record.traces
    ):
        _append_once(mismatches, "stored_trace_owner_mismatch")
    if any(
        checkpoint.session_id != record.result.session_id
        for checkpoint in record.checkpoints
    ):
        _append_once(mismatches, "stored_checkpoint_owner_mismatch")
    if mismatches == report.mismatches:
        return report
    return report.model_copy(
        update={
            "status": TraceIntegrityStatus.INVALID,
            "mismatches": mismatches,
        }
    )


def _validate_tool_name(span: TraceSpan) -> None:
    if span.tool_name is not None and not _SAFE_TOOL_NAME.fullmatch(span.tool_name):
        raise ValueError("stored tool name is not a safe identifier")


def _known_error_code(value: str | None) -> ErrorCode | None:
    if value is None:
        return None
    try:
        return ErrorCode(value)
    except ValueError:
        return None


MetricKey = TypeVar("MetricKey")


def _increment(counts: dict[MetricKey, int], key: MetricKey) -> None:
    counts[key] = counts.get(key, 0) + 1


def _sorted_enum_counts(counts: dict[MetricKey, int]) -> dict[MetricKey, int]:
    return dict(sorted(counts.items(), key=lambda item: str(item[0])))


def _sorted_string_counts(counts: dict[str, int]) -> dict[str, int]:
    return dict(sorted(counts.items()))


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
