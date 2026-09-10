from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from devbrief.application.orchestration import BugTriageApplication
from devbrief.domain.contracts import ExecutionBudget, Plan
from devbrief.integration.media import OpenAICompatibleMediaClient
from devbrief.integration.repository import WorkspaceRepositoryEvidence
from devbrief.integration.task_system import TaskSystemDraftClient
from devbrief.integration.web import create_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="devbrief")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one local fixture")
    run.add_argument("path", type=Path)
    serve = subparsers.add_parser("serve", help="start the local web demo")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    serve.add_argument("--db", default="devbrief.sqlite3", type=Path)
    transcribe = subparsers.add_parser("transcribe", help="transcribe an audio file")
    transcribe.add_argument("path", type=Path)
    transcribe.add_argument("--output", type=Path)
    speak = subparsers.add_parser("speak", help="synthesize speech from text")
    speak.add_argument("text")
    speak.add_argument("--output", type=Path, required=True)
    speak.add_argument("--voice", default="Cherry")
    approve = subparsers.add_parser(
        "approve", help="approve or reject a pending web run"
    )
    approve.add_argument("session_id")
    approve.add_argument("--approver", default="human-cli")
    approve.add_argument("--plan-hash")
    approve.add_argument("--url", default="http://127.0.0.1:8000")
    approve.add_argument("--reject", action="store_true")
    evidence = subparsers.add_parser(
        "evidence", help="read bounded repository evidence"
    )
    evidence.add_argument("path")
    draft = subparsers.add_parser("draft", help="create a task-system draft")
    draft.add_argument("plan", type=Path, help="JSON Plan payload")
    draft.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.command == "run":
        result = BugTriageApplication().run(
            args.path,
            budget=ExecutionBudget(
                max_steps=16,
                max_tool_calls=4,
                deadline_at=datetime.now(UTC) + timedelta(minutes=5),
                max_model_tokens=1000,
                max_cost=1.0,
            ),
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    if args.command == "transcribe":
        fixture = _media_client().transcribe(args.path)
        payload = json.dumps(
            fixture.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
        return
    if args.command == "speak":
        _media_client().synthesize(args.text, args.output, voice=args.voice)
        print(args.output)
        return
    if args.command == "approve":
        payload = {
            "session_id": args.session_id,
            "approver_id": args.approver,
            "approved": not args.reject,
        }
        if args.plan_hash:
            payload["plan_hash"] = args.plan_hash
        request = Request(
            f"{args.url.rstrip('/')}/api/approve",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            print(response.read().decode("utf-8"))
        return
    if args.command == "evidence":
        result = WorkspaceRepositoryEvidence(Path.cwd()).read(args.path)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return
    if args.command == "draft":
        plan = Plan.model_validate(json.loads(args.plan.read_text(encoding="utf-8")))
        result = TaskSystemDraftClient(dry_run=not args.live).create_draft(plan)
        print(
            json.dumps(
                {
                    "draft_id": result.draft_id,
                    "url": result.url,
                    "dry_run": result.dry_run,
                }
            )
        )
        return
    server = create_server(path=args.db, host=args.host, port=args.port)
    print(f"DevBrief Web Demo: http://{args.host}:{args.port}")
    server.serve_forever()


def _media_client() -> OpenAICompatibleMediaClient:
    base_url = os.getenv(
        "DEVBRIEF_BAILIAN_BASE_URL",
        "https://llm-3v3kgqdr8b0jtkjh.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    return OpenAICompatibleMediaClient(base_url=base_url)
