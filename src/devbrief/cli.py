from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from devbrief.application.benchmark import (
    DeterministicBenchmarkService,
    p95_exceeds,
    validate_max_p95_ms,
)
from devbrief.application.evaluation import (
    DeterministicEvalService,
    load_eval_artifact,
)
from devbrief.application.orchestration import BugTriageApplication
from devbrief.domain.contracts import (
    Checkpoint,
    ExecutionBudget,
    Plan,
    TraceIntegrityReport,
    TraceIntegrityStatus,
    TraceSpan,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.trace_integrity import verify_trace_integrity
from devbrief.integration.media import MediaError, OpenAICompatibleMediaClient
from devbrief.integration.repository import (
    RepositoryEvidenceError,
    WorkspaceRepositoryEvidence,
)
from devbrief.integration.storage import SQLiteRunStore
from devbrief.integration.task_system import TaskSystemDraftClient, TaskSystemError
from devbrief.integration.web import create_server


def main() -> int:
    try:
        return _main()
    except (
        DevBriefError,
        MediaError,
        RepositoryEvidenceError,
        TaskSystemError,
        HTTPError,
        URLError,
        OSError,
        ValueError,
    ) as exc:
        print(f"devbrief error: {exc}", file=sys.stderr)
        return 2


def _main() -> int:
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
    evaluation = subparsers.add_parser(
        "eval", help="run the versioned deterministic Eval baseline"
    )
    evaluation.add_argument(
        "--dataset",
        type=Path,
        default=Path("fixtures/evals/bug-triage-v1.json"),
        help="versioned public evaluation dataset",
    )
    evaluation_output = evaluation.add_mutually_exclusive_group()
    evaluation_output.add_argument("--output", type=Path)
    evaluation_output.add_argument(
        "--check", type=Path, help="fail when the result differs from a baseline"
    )
    benchmark = subparsers.add_parser(
        "benchmark", help="measure the deterministic local Bug Triage workload"
    )
    benchmark.add_argument(
        "--fixture",
        type=Path,
        default=Path("fixtures/transcripts/bug-triage-redacted-v1.json"),
        help="public redacted fixture below the fixtures root",
    )
    benchmark.add_argument(
        "--iterations",
        type=int,
        default=30,
        help="measured iterations (1-1000)",
    )
    benchmark.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="unreported warmup iterations (0-1000)",
    )
    benchmark.add_argument(
        "--max-p95-ms",
        type=float,
        help="optional local p95 gate; it is not a production SLO",
    )
    benchmark.add_argument(
        "--output", type=Path, help="write the redacted aggregate artifact"
    )
    trace_verify = subparsers.add_parser(
        "trace-verify",
        help="verify one persisted trace hash chain without replaying it",
    )
    trace_verify.add_argument("session_id")
    trace_verify.add_argument("--db", default="devbrief.sqlite3", type=Path)
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
        return 0
    if args.command == "transcribe":
        fixture = _media_client().transcribe(args.path)
        payload = json.dumps(
            fixture.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
        return 0
    if args.command == "speak":
        _media_client().synthesize(args.text, args.output, voice=args.voice)
        print(args.output)
        return 0
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
        return 0
    if args.command == "evidence":
        result = WorkspaceRepositoryEvidence(Path.cwd()).read(args.path)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
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
        return 0
    if args.command == "eval":
        artifact = DeterministicEvalService().run(args.dataset)
        if args.check:
            expected = load_eval_artifact(args.check)
            if artifact != expected:
                raise DevBriefError(
                    ErrorCode.CONFLICT,
                    "evaluation report does not match the committed baseline",
                )
        payload = artifact.model_dump_json(indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
        return 0
    if args.command == "benchmark":
        validate_max_p95_ms(args.max_p95_ms)
        artifact = DeterministicBenchmarkService().run(
            args.fixture,
            measurement_iterations=args.iterations,
            warmup_iterations=args.warmup,
        )
        payload = artifact.model_dump_json(indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
        if p95_exceeds(artifact, max_p95_ms=args.max_p95_ms):
            print(
                "devbrief benchmark gate: p95_ms exceeds --max-p95-ms",
                file=sys.stderr,
            )
            return 3
        return 0
    if args.command == "trace-verify":
        report = _stored_trace_integrity_report(args.db, args.session_id)
        print(report.model_dump_json(indent=2))
        return 0 if report.status is TraceIntegrityStatus.VERIFIED else 2
    server = create_server(path=args.db, host=args.host, port=args.port)
    print(f"DevBrief Web Demo: http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


def _media_client() -> OpenAICompatibleMediaClient:
    base_url = os.getenv(
        "DEVBRIEF_BAILIAN_BASE_URL",
        "https://llm-3v3kgqdr8b0jtkjh.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    return OpenAICompatibleMediaClient(base_url=base_url)


def _stored_trace_integrity_report(
    database: Path, session_id: str
) -> TraceIntegrityReport:
    """Load only persisted audit metadata and return a redacted chain verdict."""
    store = SQLiteRunStore(database)
    result = store.get(session_id)
    if result is None:
        raise DevBriefError(ErrorCode.VALIDATION_ERROR, "session does not exist")
    artifacts = store.get_artifacts(session_id)
    traces = _stored_trace_spans(artifacts.get("traces"))
    checkpoints = _stored_checkpoints(artifacts.get("checkpoints"))
    report = verify_trace_integrity(traces, checkpoints=checkpoints)
    if any(
        item.session_id != result.session_id or item.trace_id != result.trace_id
        for item in traces
    ):
        return report.model_copy(
            update={
                "status": TraceIntegrityStatus.INVALID,
                "mismatches": [*report.mismatches, "stored_trace_owner_mismatch"],
            }
        )
    return report


def _stored_trace_spans(value: object | None) -> tuple[TraceSpan, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "stored trace artifact is invalid"
        )
    items = cast(list[object], value)
    try:
        return tuple(TraceSpan.model_validate(item) for item in items)
    except (TypeError, ValueError) as exc:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "stored trace artifact is invalid"
        ) from exc


def _stored_checkpoints(value: object | None) -> tuple[Checkpoint, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "stored checkpoint artifact is invalid"
        )
    items = cast(list[object], value)
    try:
        return tuple(Checkpoint.model_validate(item) for item in items)
    except (TypeError, ValueError) as exc:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "stored checkpoint artifact is invalid"
        ) from exc


if __name__ == "__main__":
    raise SystemExit(main())
