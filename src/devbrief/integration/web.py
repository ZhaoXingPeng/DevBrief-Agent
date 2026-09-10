# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from devbrief.application.external_write import ControlledIssueWriter
from devbrief.application.orchestration import BugTriageApplication
from devbrief.application.receipts import InMemoryReceiptRepository
from devbrief.domain.approval import (
    ApprovalGate,
    InMemoryApprovalRepository,
    compute_plan_hash,
)
from devbrief.domain.contracts import (
    Approval,
    ApprovalStatus,
    ExecutionBudget,
    ExecutionKind,
    Plan,
    SessionState,
    ToolLevel,
    ToolRequest,
    ToolSpec,
    TriageRunResult,
)
from devbrief.domain.errors import DevBriefError
from devbrief.domain.tools import PolicyGate, ToolRegistry
from devbrief.integration.github import GitHubIssueClient, GitHubIssueProvider
from devbrief.integration.media import MediaError, OpenAICompatibleMediaClient
from devbrief.integration.storage import SQLiteRunStore

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
DEFAULT_BASE_URL = (
    "https://llm-3v3kgqdr8b0jtkjh.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)

HTML = """<!doctype html><html lang=zh-CN><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>DevBrief Agent</title>
<script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
<style>body{font:15px system-ui;max-width:1040px;margin:28px auto;padding:0 18px;color:#172321;background:#fbfdfc}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #d9e2df;padding-bottom:12px}main{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}section{border:1px solid #d9e2df;padding:16px;border-radius:8px;background:#fff}section.wide{grid-column:1/-1}button{background:#16796f;color:#fff;border:0;padding:9px 14px;border-radius:5px;cursor:pointer;margin-right:6px}button.secondary{background:#51635f}button.danger{background:#a94442}input{padding:9px;border:1px solid #b9c8c4;border-radius:4px;margin:4px 6px 4px 0;max-width:100%}pre{background:#f3f7f5;padding:14px;overflow:auto;white-space:pre-wrap;max-height:520px}audio{width:100%}.muted{color:#61716d;font-size:13px}.row{display:flex;flex-wrap:wrap;align-items:center;gap:4px}@media(max-width:720px){main{grid-template-columns:1fr}section.wide{grid-column:auto}}</style></head>
<body><div id=app><header><h1>DevBrief Agent</h1><span class=muted>{{ health }}</span></header><main>
<section><h2>运行 Triage</h2><form @submit.prevent="run"><div class=row><input type=file ref=file accept=".json,audio/*"><input v-model="path" placeholder="fixture 路径"></div><button :disabled="busy">{{ busy?'运行中...':'运行' }}</button></form><p class=muted>支持脱敏 JSON fixture 或音频上传，上传内容仅在临时文件中处理。</p></section>
<section><h2>审批与 Issue</h2><input v-model="sessionId" placeholder="session_id"><input v-model="approver" placeholder="审批人"><div><button @click="approve(true)" :disabled="!sessionId">批准并执行</button><button class="danger" @click="approve(false)" :disabled="!sessionId">拒绝</button></div></section>
<section><h2>语音播报</h2><form @submit.prevent="speak"><input v-model="speech" style="width:70%"><button>生成语音</button></form><audio ref=player controls></audio></section>
<section><h2>运行历史</h2><button class=secondary @click="loadRuns">刷新</button><pre>{{ JSON.stringify(runs,null,2) }}</pre></section>
<section class=wide><h2>运行详情</h2><pre>{{ JSON.stringify(output,null,2) }}</pre></section>
</main></div><script>const{createApp}=Vue;createApp({data:()=>({health:'检查中...',busy:false,path:'fixtures/transcripts/bug-triage-redacted-v1.json',sessionId:'',approver:'human-web',speech:'准备提交任务',output:{},runs:[]}),mounted(){fetch('/health').then(r=>r.json()).then(x=>this.health=x.status).catch(()=>this.health='不可用')},methods:{async run(){this.busy=true;try{const form=new FormData(),file=this.$refs.file.files[0];let r;if(file){form.append('file',file);r=await fetch('/api/run',{method:'POST',body:form})}else{r=await fetch('/api/run',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path:this.path})})}this.output=await r.json();if(this.output.session_id)this.sessionId=this.output.session_id;this.loadRuns()}finally{this.busy=false}},async approve(approved){const r=await fetch('/api/approve',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({session_id:this.sessionId,approver_id:this.approver,approved,plan_hash:this.output.plan_hash})});this.output=await r.json();this.loadRuns()},async loadRuns(){const r=await fetch('/api/runs');this.runs=(await r.json()).runs||[]},async speak(){const r=await fetch('/api/speak',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:this.speech})});if(r.ok)this.$refs.player.src=URL.createObjectURL(await r.blob());else this.output=await r.json()}}}).mount('#app');</script></body></html>"""


class _Record:
    def __init__(
        self, result: TriageRunResult, plan: Plan, application: BugTriageApplication
    ) -> None:
        self.result = result
        self.plan = plan
        self.application = application
        self.approvals = InMemoryApprovalRepository()
        spec = ToolSpec(
            name="create_issue",
            description="Create one GitHub Issue after approval",
            level=ToolLevel.EXTERNAL_WRITE,
            execution_kind=ExecutionKind.HTTP_ADAPTER,
            timeout_seconds=30,
            handler_key="github.create_issue",
            input_schema={
                "type": "object",
                "required": ["repository", "title", "body"],
            },
        )
        registry = ToolRegistry([spec])
        self.writer = ControlledIssueWriter(
            registry=registry,
            policy=PolicyGate(registry),
            approval_gate=ApprovalGate(self.approvals),
            receipt_repository=InMemoryReceiptRepository(),
            provider=GitHubIssueProvider(_github_client()),
            harness=application.harness,
        )
        self.approval: Approval | None = None


def create_server(
    *, path: Path = Path("devbrief.sqlite3"), host: str = "127.0.0.1", port: int = 8000
) -> ThreadingHTTPServer:
    store = SQLiteRunStore(path)
    records: dict[str, _Record] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            if route == "/health":
                self._send(200, {"status": "ok", "database": str(store.path)})
            elif route == "/api/runs":
                self._send(200, {"runs": list(store.list_metadata())})
            elif route.startswith("/api/runs/"):
                self._run_detail(route.rsplit("/", 1)[-1])
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(HTML.encode("utf-8"))

        def do_POST(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            try:
                if route == "/api/run":
                    self._run()
                elif route == "/api/transcribe":
                    self._transcribe()
                elif route == "/api/approve":
                    self._approve()
                elif route == "/api/speak":
                    self._speak()
                else:
                    self._send(404, {"error": "not found"})
            except (KeyError, TypeError, ValueError, DevBriefError, MediaError) as exc:
                self._send(400, {"error": str(exc)})

        def _run(self) -> None:
            fields, files = self._request_parts()
            fixture_path: Path | None = None
            temporary: list[Path] = []
            try:
                if "file" in files:
                    name, content, content_type = files["file"]
                    if len(content) > MAX_UPLOAD_BYTES:
                        raise ValueError("upload exceeds 10 MB limit")
                    suffix = Path(name).suffix.lower()
                    if suffix == ".json" or content_type == "application/json":
                        fixture_path = _temp_file(content, ".json")
                        temporary.append(fixture_path)
                    elif content_type.startswith("audio/") or suffix in {
                        ".wav",
                        ".mp3",
                        ".m4a",
                        ".ogg",
                        ".webm",
                    }:
                        audio_path = _temp_file(content, suffix or ".wav")
                        temporary.append(audio_path)
                        fixture = _media_client().transcribe(audio_path)
                        fixture_path = _temp_file(
                            json.dumps(fixture.model_dump(mode="json")).encode(),
                            ".json",
                        )
                        temporary.append(fixture_path)
                    else:
                        raise ValueError(
                            "only JSON fixtures and audio files are accepted"
                        )
                else:
                    raw_path = str(fields.get("path", ""))
                    fixture_path = _workspace_path(raw_path)
                run_application = BugTriageApplication()
                result = run_application.run(
                    fixture_path,
                    budget=_budget(),
                )
                plan = _external_plan(result)
                result = result.model_copy(
                    update={
                        "draft": result.draft.model_copy(
                            update={"plan": plan, "plan_hash": compute_plan_hash(plan)}
                        )
                    }
                )
                record = _Record(result, plan, run_application)
                records[result.session_id] = record
                store.save(
                    result,
                    traces=run_application.harness.traces.list_for(result.trace_id),
                    checkpoints=run_application.harness.checkpoints.list_for(
                        result.session_id
                    ),
                )
                self._send(200, _public_result(record))
            finally:
                for item in temporary:
                    item.unlink(missing_ok=True)

        def _transcribe(self) -> None:
            fields, files = self._request_parts()
            temporary: list[Path] = []
            try:
                if "file" in files:
                    name, content, content_type = files["file"]
                    if len(content) > MAX_UPLOAD_BYTES or not (
                        content_type.startswith("audio/")
                        or Path(name).suffix.lower()
                        in {".wav", ".mp3", ".m4a", ".ogg", ".webm"}
                    ):
                        raise ValueError("invalid audio upload")
                    source = _temp_file(content, Path(name).suffix or ".wav")
                    temporary.append(source)
                else:
                    source = _workspace_path(str(fields["path"]))
                fixture = _media_client().transcribe(source)
                self._send(200, fixture.model_dump(mode="json"))
            finally:
                for item in temporary:
                    item.unlink(missing_ok=True)

        def _approve(self) -> None:
            payload = self._json()
            session_id = str(payload["session_id"])
            record = records.get(session_id)
            if record is None:
                record_result = store.get(session_id)
                if record_result is None:
                    raise ValueError("session does not exist")
                raise ValueError(
                    "session is not active; restart requires a new approval"
                )
            approver = str(payload.get("approver_id", "human"))
            approved = bool(payload.get("approved", False))
            if not approved:
                record.approval = Approval(
                    approval_id=f"apr_{session_id}",
                    plan_hash=compute_plan_hash(record.plan),
                    scope=["create_issue"],
                    approver_id=approver,
                    status=ApprovalStatus.REJECTED,
                    expires_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                )
                record.approvals.save(record.approval)
                record.application.harness.transition(session_id, SessionState.REJECTED)
                record.result = record.result.model_copy(
                    update={
                        "state": SessionState.REJECTED,
                        "approval_status": ApprovalStatus.REJECTED,
                        "approval_id": record.approval.approval_id,
                    }
                )
                store.save(
                    record.result,
                    approval=record.approval,
                    traces=record.application.harness.traces.list_for(
                        record.result.trace_id
                    ),
                    checkpoints=record.application.harness.checkpoints.list_for(
                        session_id
                    ),
                )
                self._send(200, _public_result(record))
                return
            expected_hash = compute_plan_hash(record.plan)
            if payload.get("plan_hash") not in (None, expected_hash):
                raise ValueError("plan hash does not match current plan")
            record.approval = Approval(
                approval_id=f"apr_{session_id}",
                plan_hash=expected_hash,
                scope=["create_issue"],
                approver_id=approver,
                status=ApprovalStatus.APPROVED,
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                created_at=datetime.now(UTC),
            )
            record.approvals.save(record.approval)
            record.application.harness.transition(session_id, SessionState.EXECUTING)
            request = ToolRequest(
                tool_call_id=f"call_{session_id}",
                tool_name="create_issue",
                arguments=record.plan.arguments,
                session_id=session_id,
                trace_id=record.result.trace_id,
                approval_id=record.approval.approval_id,
                plan_hash=expected_hash,
                idempotency_key=f"idem_{hashlib.sha256((session_id + expected_hash).encode()).hexdigest()[:24]}",
            )
            outcome = record.writer.execute(request, record.plan)
            if outcome.allowed and outcome.receipt is not None:
                record.application.harness.transition(
                    session_id, SessionState.COMPLETED
                )
                record.result = record.result.model_copy(
                    update={
                        "state": SessionState.COMPLETED,
                        "approval_status": ApprovalStatus.CONSUMED,
                        "approval_id": record.approval.approval_id,
                        "receipt": outcome.receipt,
                    }
                )
            else:
                record.result = record.result.model_copy(
                    update={
                        "state": SessionState.EXECUTING,
                        "approval_status": record.approval.status,
                        "approval_id": record.approval.approval_id,
                        "error": outcome.reason,
                    }
                )
            store.save(
                record.result,
                approval=record.approval,
                receipt=outcome.receipt,
                traces=record.application.harness.traces.list_for(
                    record.result.trace_id
                ),
                checkpoints=record.application.harness.checkpoints.list_for(session_id),
            )
            self._send(
                200,
                _public_result(record) | {"outcome": outcome.model_dump(mode="json")},
            )

        def _speak(self) -> None:
            text = str(self._json()["text"])
            target = Path(tempfile.mkstemp(suffix=".mp3")[1])
            try:
                _media_client().synthesize(text, target)
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            finally:
                target.unlink(missing_ok=True)

        def _run_detail(self, session_id: str) -> None:
            record = records.get(session_id)
            if record is None:
                result = store.get(session_id)
                if result is None:
                    self._send(404, {"error": "session does not exist"})
                    return
                self._send(
                    200,
                    {
                        "result": result.model_dump(mode="json"),
                        "artifacts": store.get_artifacts(session_id),
                    },
                )
                return
            self._send(200, _public_result(record))

        def _request_parts(
            self,
        ) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
            content_type = self.headers.get("Content-Type", "")
            size = int(self.headers.get("Content-Length", "0"))
            if size > MAX_UPLOAD_BYTES + 1024 * 1024:
                raise ValueError("request is too large")
            body = self.rfile.read(size)
            if content_type.startswith("multipart/form-data"):
                return _multipart(content_type, body)
            raw_payload: Any = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(raw_payload, dict):
                raise ValueError("JSON object required")
            payload = cast(dict[str, Any], raw_payload)
            return {str(k): str(v) for k, v in payload.items()}, {}

        def _json(self) -> dict[str, Any]:
            size = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return cast(dict[str, Any], value)

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


def _budget() -> ExecutionBudget:
    return ExecutionBudget(
        max_steps=16,
        max_tool_calls=4,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        max_model_tokens=1000,
        max_cost=1.0,
    )


def _external_plan(result: TriageRunResult) -> Plan:
    source = result.draft.plan
    return Plan(
        title=source.title,
        body=source.body,
        repository=os.getenv("DEVBRIEF_GITHUB_REPOSITORY", "owner/repository"),
        labels=source.labels,
        assignee=source.assignee,
        tool_name="create_issue",
        arguments={
            "repository": os.getenv("DEVBRIEF_GITHUB_REPOSITORY", "owner/repository"),
            "title": source.title,
            "body": source.body,
            "labels": source.labels,
            "assignee": source.assignee,
        },
    )


def _public_result(record: _Record) -> dict[str, object]:
    result = record.result.model_dump(mode="json")
    result["execution_plan"] = record.plan.model_dump(mode="json")
    result["plan_hash"] = compute_plan_hash(record.plan)
    result["trace_summary"] = {
        "trace_id": record.result.trace_id,
        "spans": len(
            record.application.harness.traces.list_for(record.result.trace_id)
        ),
    }
    result["checkpoint_summary"] = {
        "count": len(
            record.application.harness.checkpoints.list_for(record.result.session_id)
        )
    }
    return result


def _github_client() -> GitHubIssueClient:
    value = os.getenv("DEVBRIEF_GITHUB_DRY_RUN", "true").casefold()
    return GitHubIssueClient(dry_run=value not in {"0", "false", "no"})


def _media_client() -> OpenAICompatibleMediaClient:
    return OpenAICompatibleMediaClient(
        base_url=os.getenv("DEVBRIEF_BAILIAN_BASE_URL", DEFAULT_BASE_URL)
    )


def _workspace_path(value: str) -> Path:
    if not value:
        raise ValueError("path is required")
    path = Path(value).resolve()
    workspace = Path.cwd().resolve()
    if workspace != path and workspace not in path.parents:
        raise ValueError("path must remain inside workspace")
    return path


def _temp_file(content: bytes, suffix: str) -> Path:
    handle, name = tempfile.mkstemp(prefix="devbrief-", suffix=suffix)
    os.close(handle)
    path = Path(name)
    path.write_bytes(content)
    return path


def _multipart(
    content_type: str, body: bytes
) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    match = re.search(r"boundary=\"?([^;\"]+)", content_type)
    if not match:
        raise ValueError("multipart boundary is required")
    boundary = b"--" + match.group(1).encode()
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes, str]] = {}
    for chunk in body.split(boundary)[1:]:
        chunk = chunk.strip(b"\r\n-")
        if not chunk or b"\r\n\r\n" not in chunk:
            continue
        raw_headers, value = chunk.split(b"\r\n\r\n", 1)
        headers = raw_headers.decode("utf-8", "replace").split("\r\n")
        disposition = next(
            (
                line
                for line in headers
                if line.lower().startswith("content-disposition:")
            ),
            "",
        )
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        file_match = re.search(r'filename="([^"]*)"', disposition)
        ctype = next(
            (
                line.split(":", 1)[1].strip()
                for line in headers
                if line.lower().startswith("content-type:")
            ),
            "text/plain",
        )
        if file_match:
            files[name] = (file_match.group(1), value.rstrip(b"\r\n"), ctype)
        else:
            fields[name] = value.rstrip(b"\r\n").decode("utf-8")
    return fields, files
