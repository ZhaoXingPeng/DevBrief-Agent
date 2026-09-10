from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from devbrief.integration.web import create_server


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
