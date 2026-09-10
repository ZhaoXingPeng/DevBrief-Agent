from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from devbrief.integration.github import GitHubError
from devbrief.integration.web import create_server, github_client_from_environment


def test_web_json_run_approve_and_history(tmp_path: Path) -> None:
    server = create_server(path=tmp_path / "runs.sqlite3", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        payload = json.dumps(
            {"path": "fixtures/transcripts/bug-triage-redacted-v1.json"}
        ).encode()
        result = json.loads(
            urlopen(
                Request(
                    f"{base}/api/run",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            ).read()
        )
        assert result["state"] == "awaiting_approval"
        approval = json.dumps(
            {
                "session_id": result["session_id"],
                "approver_id": "test",
                "approved": True,
                "plan_hash": result["plan_hash"],
            }
        ).encode()
        completed = json.loads(
            urlopen(
                Request(
                    f"{base}/api/approve",
                    data=approval,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            ).read()
        )
        assert completed["state"] == "completed"
        assert completed["receipt"]["provider_request_id"] == "dry-run"
        history = json.loads(urlopen(f"{base}/api/runs").read())
        assert history["runs"][0]["session_id"] == result["session_id"]
    finally:
        server.shutdown()
        server.server_close()


def test_web_evidence_and_draft_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text(
        "token=private-value\nDecision", encoding="utf-8"
    )
    server = create_server(path=tmp_path / "runs.sqlite3", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        headers = {"Content-Type": "application/json"}
        evidence = json.loads(
            urlopen(
                Request(
                    f"{base}/api/evidence",
                    data=json.dumps({"path": "notes.txt"}).encode(),
                    headers=headers,
                    method="POST",
                )
            ).read()
        )
        assert evidence["reference"] == "repo://notes.txt"
        assert "private-value" not in evidence["summary"]
        draft = json.loads(
            urlopen(
                Request(
                    f"{base}/api/draft",
                    data=json.dumps(
                        {
                            "plan": {
                                "title": "Task",
                                "body": "Details",
                                "repository": "owner/repository",
                                "tool_name": "create_issue",
                            }
                        }
                    ).encode(),
                    headers=headers,
                    method="POST",
                )
            ).read()
        )
        assert draft["dry_run"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_web_approval_rejects_string_boolean(tmp_path: Path) -> None:
    server = create_server(path=tmp_path / "runs.sqlite3", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        payload = json.dumps({"session_id": "missing", "approved": "false"}).encode()
        request = Request(
            f"{base}/api/approve",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert b"approved must be a boolean" in error.value.read()
    finally:
        server.shutdown()
        server.server_close()


def test_web_live_github_rejects_when_repository_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVBRIEF_GITHUB_DRY_RUN", "false")
    monkeypatch.setenv("DEVBRIEF_GITHUB_TOKEN", "test-token")
    monkeypatch.delenv("DEVBRIEF_GITHUB_REPOSITORY", raising=False)

    with pytest.raises(GitHubError, match="scope"):
        github_client_from_environment().create_issue(
            repository="owner/repository", title="Task", body="Details"
        )
