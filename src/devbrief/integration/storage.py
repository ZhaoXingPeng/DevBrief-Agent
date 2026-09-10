from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from devbrief.domain.contracts import TriageRunResult


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

    def save(self, result: TriageRunResult) -> None:
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
                INSERT INTO triage_runs(session_id, trace_id, state, result_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    trace_id=excluded.trace_id,
                    state=excluded.state,
                    result_json=excluded.result_json
                """,
                (result.session_id, result.trace_id, result.state.value, payload),
            )

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
