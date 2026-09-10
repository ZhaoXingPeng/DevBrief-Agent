from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from devbrief.domain.approval import compute_plan_hash
from devbrief.domain.contracts import (
    Approval,
    ApprovalStatus,
    SessionState,
    ToolReceipt,
    ToolReceiptStatus,
    TranscriptFixture,
    TranscriptSegment,
)
from devbrief.integration import web as web_module
from devbrief.integration.github import GitHubError, GitHubIssue
from devbrief.integration.storage import SQLiteRunStore
from devbrief.integration.web import create_server, github_client_from_environment


def _multipart_request(
    base: str,
    endpoint: str,
    *,
    filename: str,
    content_type: str,
    content: bytes,
) -> Request:
    boundary = "devbrief-upload-test"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return Request(
        f"{base}{endpoint}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )


@pytest.mark.parametrize(
    ("name", "content_type", "content"),
    [
        ("audio.wav", "audio/wav", b"RIFF\x00\x00\x00\x00WAVE"),
        ("audio.mp3", "audio/mpeg", b"ID3\x04\x00\x00"),
        ("audio.m4a", "audio/mp4", b"\x00\x00\x00\x14ftypM4A "),
        ("audio.ogg", "audio/ogg", b"OggS\x00\x02"),
        ("audio.webm", "audio/webm", b"\x1aE\xdf\xa3webm"),
    ],
)
def test_audio_upload_validation_accepts_known_format_signatures(
    name: str, content_type: str, content: bytes
) -> None:
    assert (
        web_module.validate_audio_upload(name, content, content_type)
        == Path(name).suffix
    )


@pytest.mark.parametrize("endpoint", ["/api/run", "/api/transcribe"])
@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("not-audio.wav", "audio/wav", b"not a WAV file"),
        ("wrong-extension.txt", "audio/wav", b"RIFF\x00\x00\x00\x00WAVE"),
    ],
)
def test_web_rejects_unverified_audio_before_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    def unexpected_media_client() -> object:
        raise AssertionError("unverified audio must not reach the media provider")

    monkeypatch.setattr(web_module, "_media_client", unexpected_media_client)
    server = create_server(path=tmp_path / "runs.sqlite3", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(HTTPError) as error:
            urlopen(
                _multipart_request(
                    base,
                    endpoint,
                    filename=filename,
                    content_type=content_type,
                    content=content,
                )
            )
        assert error.value.code == 400
        assert b"invalid audio upload" in error.value.read()
    finally:
        server.shutdown()
        server.server_close()


def test_web_transcribes_verified_audio_and_removes_temporary_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploaded_paths: list[Path] = []

    class MediaClient:
        def transcribe(self, path: Path) -> TranscriptFixture:
            uploaded_paths.append(path)
            return TranscriptFixture(
                fixture_id="verified-audio",
                fixture_version="1.0.0",
                redacted=True,
                segments=[
                    TranscriptSegment(
                        segment_id="seg-1",
                        start_ms=0,
                        end_ms=1000,
                        speaker="unknown",
                        text="redacted decision",
                    )
                ],
            )

    monkeypatch.setattr(web_module, "_media_client", MediaClient)
    server = create_server(path=tmp_path / "runs.sqlite3", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        response = json.loads(
            urlopen(
                _multipart_request(
                    base,
                    "/api/transcribe",
                    filename="verified.wav",
                    content_type="audio/wav",
                    content=b"RIFF\x00\x00\x00\x00WAVE",
                )
            ).read()
        )
        assert response["fixture_id"] == "verified-audio"
        assert uploaded_paths
        deadline = time.monotonic() + 1.0
        while uploaded_paths[0].exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not uploaded_paths[0].exists()
    finally:
        server.shutdown()
        server.server_close()


def test_web_preserves_multipart_audio_bytes_before_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploaded_content: list[bytes] = []

    class MediaClient:
        def transcribe(self, path: Path) -> TranscriptFixture:
            uploaded_content.append(path.read_bytes())
            return TranscriptFixture(
                fixture_id="byte-preservation",
                fixture_version="1.0.0",
                redacted=True,
                segments=[
                    TranscriptSegment(
                        segment_id="seg-1",
                        start_ms=0,
                        end_ms=1000,
                        speaker="unknown",
                        text="redacted decision",
                    )
                ],
            )

    original = b"RIFF\x00\x00\x00\x00WAVE\x00\xff\r\n-"
    monkeypatch.setattr(web_module, "_media_client", MediaClient)
    server = create_server(path=tmp_path / "runs.sqlite3", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        response = json.loads(
            urlopen(
                _multipart_request(
                    base,
                    "/api/transcribe",
                    filename="preserved.wav",
                    content_type="audio/wav",
                    content=original,
                )
            ).read()
        )
        assert response["fixture_id"] == "byte-preservation"
        assert uploaded_content == [original]
    finally:
        server.shutdown()
        server.server_close()


def test_web_serves_packaged_structured_audit_console(tmp_path: Path) -> None:
    server = create_server(path=tmp_path / "runs.sqlite3", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        page = urlopen(base).read().decode("utf-8")
        match = re.search(r'src="(?P<asset>/assets/[^\"]+\.js)"', page)

        assert match is not None
        script = urlopen(f"{base}{match.group('asset')}").read().decode("utf-8")
        assert "候选与证据" in script
        assert "执行计划" in script
        assert "审批与回执" in script
        assert "运行历史" in script
    finally:
        server.shutdown()
        server.server_close()


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


def test_web_restarts_and_approves_persisted_session(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite3"
    first = create_server(path=database, port=0)
    first_thread = threading.Thread(target=first.serve_forever, daemon=True)
    first_thread.start()
    try:
        base = f"http://127.0.0.1:{first.server_port}"
        result = json.loads(
            urlopen(
                Request(
                    f"{base}/api/run",
                    data=json.dumps(
                        {"path": "fixtures/transcripts/bug-triage-redacted-v1.json"}
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            ).read()
        )
    finally:
        first.shutdown()
        first.server_close()

    second = create_server(path=database, port=0)
    second_thread = threading.Thread(target=second.serve_forever, daemon=True)
    second_thread.start()
    try:
        base = f"http://127.0.0.1:{second.server_port}"
        detail = json.loads(urlopen(f"{base}/api/runs/{result['session_id']}").read())
        assert detail["state"] == "awaiting_approval"
        approval = json.dumps(
            {
                "session_id": result["session_id"],
                "approver_id": "restart-test",
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
    finally:
        second.shutdown()
        second.server_close()


def test_web_restart_preserves_completed_receipt_without_replaying_write(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runs.sqlite3"
    first = create_server(path=database, port=0)
    first_thread = threading.Thread(target=first.serve_forever, daemon=True)
    first_thread.start()
    try:
        base = f"http://127.0.0.1:{first.server_port}"
        result = json.loads(
            urlopen(
                Request(
                    f"{base}/api/run",
                    data=json.dumps(
                        {"path": "fixtures/transcripts/bug-triage-redacted-v1.json"}
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            ).read()
        )
        approval = json.dumps(
            {
                "session_id": result["session_id"],
                "approver_id": "receipt-test",
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
        assert completed["receipt"]["idempotency_key"]
    finally:
        first.shutdown()
        first.server_close()

    second = create_server(path=database, port=0)
    second_thread = threading.Thread(target=second.serve_forever, daemon=True)
    second_thread.start()
    try:
        base = f"http://127.0.0.1:{second.server_port}"
        detail = json.loads(urlopen(f"{base}/api/runs/{result['session_id']}").read())
        assert detail["state"] == "completed"
        assert detail["receipt"]["provider_request_id"] == "dry-run"
        request = Request(
            f"{base}/api/approve",
            data=approval,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError):
            urlopen(request)
    finally:
        second.shutdown()
        second.server_close()


def test_web_restart_resumes_an_approved_executing_session(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite3"
    first = create_server(path=database, port=0)
    first_thread = threading.Thread(target=first.serve_forever, daemon=True)
    first_thread.start()
    try:
        base = f"http://127.0.0.1:{first.server_port}"
        result_payload = json.loads(
            urlopen(
                Request(
                    f"{base}/api/run",
                    data=json.dumps(
                        {"path": "fixtures/transcripts/bug-triage-redacted-v1.json"}
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            ).read()
        )
    finally:
        first.shutdown()
        first.server_close()

    triage = SQLiteRunStore(database).get(result_payload["session_id"])
    assert triage is not None
    plan_hash = compute_plan_hash(triage.draft.plan)
    approval = Approval(
        approval_id=f"apr_{triage.session_id}",
        plan_hash=plan_hash,
        scope=["create_issue"],
        approver_id="crash-recovery",
        status=ApprovalStatus.APPROVED,
        expires_at=triage.budget.deadline_at,
        created_at=triage.budget.deadline_at,
    )
    executing = triage.model_copy(
        update={
            "state": SessionState.EXECUTING,
            "approval_status": ApprovalStatus.APPROVED,
            "approval_id": approval.approval_id,
        }
    )
    SQLiteRunStore(database).save(executing, approval=approval)

    second = create_server(path=database, port=0)
    second_thread = threading.Thread(target=second.serve_forever, daemon=True)
    second_thread.start()
    try:
        base = f"http://127.0.0.1:{second.server_port}"
        request = Request(
            f"{base}/api/approve",
            data=json.dumps(
                {
                    "session_id": triage.session_id,
                    "approved": True,
                    "plan_hash": plan_hash,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resumed = json.loads(urlopen(request).read())
        assert resumed["state"] == "completed"
        assert resumed["receipt"]["provider_request_id"] == "dry-run"
    finally:
        second.shutdown()
        second.server_close()


def test_web_recover_queries_persisted_unknown_github_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "runs.sqlite3"
    first = create_server(path=database, port=0)
    first_thread = threading.Thread(target=first.serve_forever, daemon=True)
    first_thread.start()
    try:
        base = f"http://127.0.0.1:{first.server_port}"
        payload = json.dumps(
            {"path": "fixtures/transcripts/bug-triage-redacted-v1.json"}
        ).encode()
        result_payload = json.loads(
            urlopen(
                Request(
                    f"{base}/api/run",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            ).read()
        )
    finally:
        first.shutdown()
        first.server_close()

    triage = SQLiteRunStore(database).get(result_payload["session_id"])
    assert triage is not None
    plan_hash = compute_plan_hash(triage.draft.plan)
    approval = Approval(
        approval_id=f"apr_{triage.session_id}",
        plan_hash=plan_hash,
        scope=["create_issue"],
        approver_id="provider-recovery",
        status=ApprovalStatus.CONSUMED,
        expires_at=triage.budget.deadline_at,
        created_at=triage.budget.deadline_at,
    )
    unknown = ToolReceipt(
        receipt_id="rcpt_unknown",
        tool_call_id=f"call_{triage.session_id}",
        tool_name="create_issue",
        status=ToolReceiptStatus.UNKNOWN_OUTCOME,
        provider_request_id="github-unknown-1",
        idempotency_key="idem-recovery-test",
        created_at=triage.budget.deadline_at,
    )
    SQLiteRunStore(database).save(
        triage.model_copy(
            update={
                "state": SessionState.EXECUTING,
                "approval_status": ApprovalStatus.CONSUMED,
                "approval_id": approval.approval_id,
                "receipt": unknown,
            }
        ),
        approval=approval,
        receipt=unknown,
    )

    class QueryClient:
        def find_issue_by_idempotency(
            self, *, repository: str, idempotency_key: str
        ) -> GitHubIssue | None:
            assert repository == triage.draft.plan.repository
            assert idempotency_key == unknown.idempotency_key
            return GitHubIssue(
                number=99,
                url="https://github.com/owner/repository/issues/99",
                title="Recovered",
                dry_run=False,
            )

    monkeypatch.setattr(web_module, "github_client_from_environment", QueryClient)
    second = create_server(path=database, port=0)
    second_thread = threading.Thread(target=second.serve_forever, daemon=True)
    second_thread.start()
    try:
        base = f"http://127.0.0.1:{second.server_port}"
        request = Request(
            f"{base}/api/recover",
            data=json.dumps({"session_id": triage.session_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        recovered = json.loads(urlopen(request).read())
        assert recovered["state"] == "completed"
        assert recovered["receipt"]["external_object_id"] == "99"
    finally:
        second.shutdown()
        second.server_close()


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
