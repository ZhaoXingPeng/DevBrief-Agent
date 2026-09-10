from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from devbrief.application.orchestration import BugTriageApplication
from devbrief.domain.contracts import ExecutionBudget
from devbrief.integration.media import OpenAICompatibleMediaClient
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
    server = create_server(path=args.db, host=args.host, port=args.port)
    print(f"DevBrief Web Demo: http://{args.host}:{args.port}")
    server.serve_forever()


def _media_client() -> OpenAICompatibleMediaClient:
    base_url = os.getenv(
        "DEVBRIEF_BAILIAN_BASE_URL",
        "https://llm-3v3kgqdr8b0jtkjh.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    return OpenAICompatibleMediaClient(base_url=base_url)
