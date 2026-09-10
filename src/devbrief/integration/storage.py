# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

from devbrief.domain.contracts import (
    Approval,
    Checkpoint,
    ToolReceipt,
    TraceSpan,
    TriageRunResult,
)


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
