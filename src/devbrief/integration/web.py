# ruff: noqa: E501

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from devbrief.application.orchestration import BugTriageApplication
from devbrief.domain.contracts import ExecutionBudget
from devbrief.domain.errors import DevBriefError
from devbrief.integration.media import MediaError, OpenAICompatibleMediaClient
from devbrief.integration.storage import SQLiteRunStore

HTML = """<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>DevBrief Demo</title>
<style>body{font:16px system-ui;max-width:960px;margin:40px auto;padding:0 20px;color:#152225}button{background:#1e8f83;color:white;border:0;padding:10px 16px;border-radius:6px;cursor:pointer}input{padding:10px;width:70%}pre{background:#f2f5f3;padding:16px;overflow:auto;border-radius:6px}</style>
<h1>DevBrief Agent</h1><p>提交脱敏 fixture 或音频路径，查看证据驱动任务计划。</p>
<form id=f><input name=path placeholder=\"fixtures/transcripts/bug-triage-redacted-v1.json\" required><button>运行 Triage</button></form>
<form id=a><input name=path placeholder=\"meeting.wav\" required><button>ASR 转写</button></form><pre id=o>等待运行...</pre>
<script>async function post(url,path){o.textContent='运行中...';let r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path})});o.textContent=JSON.stringify(await r.json(),null,2)};f.onsubmit=e=>{e.preventDefault();post('/api/run',f.path.value)};a.onsubmit=e=>{e.preventDefault();post('/api/transcribe',a.path.value)}</script></html>"""


def create_server(
    *, path: Path = Path("devbrief.sqlite3"), host: str = "127.0.0.1", port: int = 8000
) -> ThreadingHTTPServer:
    store = SQLiteRunStore(path)
    application = BugTriageApplication()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send(200, {"status": "ok"})
            elif self.path == "/api/runs":
                self._send(200, {"runs": list(store.list_metadata())})
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(HTML.encode("utf-8"))

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/run":
                if self.path == "/api/transcribe":
                    self._transcribe()
                    return
                self._send(404, {"error": "not found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                payload: Any = json.loads(self.rfile.read(size).decode("utf-8"))
                fixture_path = Path(str(payload["path"])).resolve()
                workspace = Path.cwd().resolve()
                if workspace not in fixture_path.parents and fixture_path != workspace:
                    raise ValueError("fixture path must remain inside workspace")
                result = application.run(
                    fixture_path,
                    budget=ExecutionBudget(
                        max_steps=16,
                        max_tool_calls=4,
                        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
                        max_model_tokens=1000,
                        max_cost=1.0,
                    ),
                )
                store.save(result)
                self._send(200, result.model_dump(mode="json"))
            except (KeyError, TypeError, ValueError, DevBriefError) as exc:
                self._send(400, {"error": str(exc)})

        def _transcribe(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                payload: Any = json.loads(self.rfile.read(size).decode("utf-8"))
                audio_path = Path(str(payload["path"])).resolve()
                workspace = Path.cwd().resolve()
                if workspace not in audio_path.parents and audio_path != workspace:
                    raise ValueError("audio path must remain inside workspace")
                client = OpenAICompatibleMediaClient(
                    base_url=os.getenv(
                        "DEVBRIEF_BAILIAN_BASE_URL",
                        "https://llm-3v3kgqdr8b0jtkjh.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                    )
                )
                fixture = client.transcribe(audio_path)
                self._send(200, fixture.model_dump(mode="json"))
            except (KeyError, TypeError, ValueError, MediaError) as exc:
                self._send(400, {"error": str(exc)})

        def _send(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return ThreadingHTTPServer((host, port), Handler)
