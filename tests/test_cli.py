from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch

from devbrief import cli
from devbrief.application.orchestration import BugTriageApplication
from devbrief.domain.contracts import ExecutionBudget
from devbrief.integration.storage import SQLiteRunStore


def test_cli_returns_two_for_user_facing_input_errors(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["devbrief", "draft", "missing-plan.json"])

    assert cli.main() == 2
    assert "devbrief error:" in capsys.readouterr().err


def test_cli_trace_verify_reports_a_persisted_verified_trace(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    application = BugTriageApplication()
    result = application.run(
        Path("fixtures/transcripts/bug-triage-redacted-v1.json"),
        budget=ExecutionBudget(
            max_steps=16,
            max_tool_calls=4,
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            max_model_tokens=1000,
            max_cost=1.0,
        ),
    )
    database = tmp_path / "runs.sqlite3"
    SQLiteRunStore(database).save(
        result,
        traces=application.harness.traces.list_for(result.trace_id),
        checkpoints=application.harness.checkpoints.list_for(result.session_id),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devbrief",
            "trace-verify",
            result.session_id,
            "--db",
            str(database),
        ],
    )

    assert cli.main() == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "verified"
    assert "bug triage" not in output.casefold()
