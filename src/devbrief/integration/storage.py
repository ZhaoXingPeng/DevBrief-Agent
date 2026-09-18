# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from devbrief.domain.contracts import (
    Approval,
    Checkpoint,
    ToolReceipt,
    TraceSpan,
    TriageRunResult,
)


@dataclass(frozen=True, slots=True)
class StoredRunRecord:
    """Validated result and audit artifacts read without mutating SQLite."""

    result: TriageRunResult
    traces: tuple[TraceSpan, ...]
    checkpoints: tuple[Checkpoint, ...]


class SQLiteRunStore:
    """Small durable store for redacted triage metadata and result payloads."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS triage_runs (
                    session_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(triage_runs)")
            }
            for name in (
                "approval_json",
                "receipt_json",
                "trace_json",
                "checkpoint_json",
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE triage_runs ADD COLUMN {name} TEXT"
                    )

    def save(
        self,
        result: TriageRunResult,
        *,
        approval: Approval | None = None,
        receipt: ToolReceipt | None = None,
        traces: tuple[TraceSpan, ...] = (),
        checkpoints: tuple[Checkpoint, ...] = (),
    ) -> None:
        self.initialize()
        payload = json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO triage_runs(
                    session_id, trace_id, state, result_json,
                    approval_json, receipt_json, trace_json, checkpoint_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    trace_id=excluded.trace_id,
                    state=excluded.state,
                    result_json=excluded.result_json,
                    approval_json=COALESCE(excluded.approval_json, triage_runs.approval_json),
                    receipt_json=COALESCE(excluded.receipt_json, triage_runs.receipt_json),
                    trace_json=COALESCE(excluded.trace_json, triage_runs.trace_json),
                    checkpoint_json=COALESCE(excluded.checkpoint_json, triage_runs.checkpoint_json)
                """,
                (
                    result.session_id,
                    result.trace_id,
                    result.state.value,
                    payload,
                    _dump(approval),
                    _dump(receipt),
                    _dump(list(traces)) if traces else None,
                    _dump(list(checkpoints)) if checkpoints else None,
                ),
            )

    def get_artifacts(self, session_id: str) -> dict[str, object | None]:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT approval_json, receipt_json, trace_json, checkpoint_json "
                "FROM triage_runs WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return {}
        return {
            "approval": _load(row[0]),
            "receipt": _load(row[1]),
            "traces": _load(row[2]) or [],
            "checkpoints": _load(row[3]) or [],
        }

    def get(self, session_id: str) -> TriageRunResult | None:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT result_json FROM triage_runs WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return TriageRunResult.model_validate(json.loads(row[0]))

    def list_metadata(self) -> tuple[Mapping[str, str], ...]:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT session_id, trace_id, state, created_at "
                "FROM triage_runs ORDER BY created_at, session_id"
            ).fetchall()
        return tuple(
            {
                "session_id": str(row[0]),
                "trace_id": str(row[1]),
                "state": str(row[2]),
                "created_at": str(row[3]),
            }
            for row in rows
        )

    def list_records_for_metrics(self) -> tuple[StoredRunRecord, ...]:
        """Read all metric inputs through a SQLite read-only connection.

        This intentionally does not call ``initialize``: callers can inspect an
        existing database without creating it or applying a migration.
        """
        if not self.path.is_file():
            raise FileNotFoundError("metrics database does not exist")
        database_uri = f"{self.path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(triage_runs)")
            }
            required = {"session_id", "trace_id", "state", "result_json"}
            if not required.issubset(columns):
                raise ValueError("metrics database has no compatible run records")
            traces_column = "trace_json" if "trace_json" in columns else "NULL"
            checkpoints_column = (
                "checkpoint_json" if "checkpoint_json" in columns else "NULL"
            )
            rows = connection.execute(
                "SELECT session_id, trace_id, state, result_json, "
                f"{traces_column}, {checkpoints_column} "
                "FROM triage_runs ORDER BY session_id"
            ).fetchall()
        return tuple(_stored_record(row) for row in rows)


def _dump(value: object | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    elif isinstance(value, (list, tuple)):
        items = list(cast(Iterable[Any], value))
        value = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in items
        ]
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _load(value: str | None) -> object | None:
    return json.loads(value) if value else None


def _stored_record(row: object) -> StoredRunRecord:
    values = cast(tuple[object, ...], row)
    if len(values) != 6:
        raise ValueError("metrics record has an invalid shape")
    session_id, trace_id, state, result_json, traces_json, checkpoints_json = values
    if not all(isinstance(item, str) for item in (session_id, trace_id, state)):
        raise ValueError("metrics record has an invalid identity")
    result = TriageRunResult.model_validate(_json_object(result_json))
    if (
        result.session_id != session_id
        or result.trace_id != trace_id
        or result.state.value != state
    ):
        raise ValueError("metrics record does not match its persisted identity")
    return StoredRunRecord(
        result=result,
        traces=tuple(
            TraceSpan.model_validate(item) for item in _json_list(traces_json)
        ),
        checkpoints=tuple(
            Checkpoint.model_validate(item) for item in _json_list(checkpoints_json)
        ),
    )


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        raise ValueError("metrics result artifact is invalid")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("metrics result artifact is invalid")
    return cast(dict[str, object], parsed)


def _json_list(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, str):
        raise ValueError("metrics audit artifact is invalid")
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("metrics audit artifact is invalid")
    return cast(list[object], parsed)
