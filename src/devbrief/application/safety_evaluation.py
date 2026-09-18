from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from devbrief.application.external_write import ControlledIssueWriter, FakeIssueProvider
from devbrief.application.harness import Harness
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
    ExternalWriteOutcome,
    Plan,
    SafetyEvalArtifact,
    SafetyEvalDataset,
    SafetyEvalReport,
    SafetyEvalScenario,
    SafetyMismatchCode,
    SafetyScenarioKind,
    SafetyScenarioResult,
    SessionState,
    ToolLevel,
    ToolReceiptStatus,
    ToolRequest,
    ToolSpec,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.tools import PolicyGate, ToolRegistry

_RUNNER_VERSION = "safety-harness-runner-1.0.0"
_DEFAULT_NOW = datetime(2026, 9, 18, tzinfo=UTC)

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class _ObservedScenario:
    allowed: bool
    error_code: ErrorCode | None
    provider_create_calls: int
    provider_query_calls: int
    receipt_status: ToolReceiptStatus | None


@dataclass(frozen=True, slots=True)
class _SafetyEnvironment:
    writer: ControlledIssueWriter
    provider: FakeIssueProvider
    harness: Harness
    plan: Plan
    request: ToolRequest


class DeterministicSafetyEvalService:
    """Run fixed no-credential Harness safety drills and emit aggregate evidence."""

    def __init__(self, *, now: Clock | None = None) -> None:
        self._now = now or (lambda: _DEFAULT_NOW)

    def run(self, dataset_path: Path) -> SafetyEvalArtifact:
        """Execute only allowlisted in-memory scenarios from a validated dataset."""
        dataset = load_safety_eval_dataset(dataset_path)
        results = [self._evaluate(scenario) for scenario in dataset.scenarios]
        failure_counts: dict[SafetyMismatchCode, int] = {}
        for result in results:
            for code in result.mismatch_codes:
                failure_counts[code] = failure_counts.get(code, 0) + 1
        return SafetyEvalArtifact(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            runner_version=_RUNNER_VERSION,
            report=SafetyEvalReport(
                scenario_version=dataset.scenarios[0].scenario_version,
                scenario_count=len(results),
                passed_count=sum(item.passed for item in results),
                failed_count=sum(not item.passed for item in results),
                failure_counts=dict(sorted(failure_counts.items())),
                results=results,
            ),
        )

    def _evaluate(self, scenario: SafetyEvalScenario) -> SafetyScenarioResult:
        observed = self._execute(scenario.kind)
        expected = scenario.expected
        mismatch_codes: list[SafetyMismatchCode] = []
        if observed.allowed != expected.allowed:
            mismatch_codes.append(SafetyMismatchCode.ALLOWED_MISMATCH)
        if observed.error_code != expected.error_code:
            mismatch_codes.append(SafetyMismatchCode.ERROR_CODE_MISMATCH)
        if observed.provider_create_calls != expected.provider_create_calls:
            mismatch_codes.append(SafetyMismatchCode.PROVIDER_CREATE_CALLS_MISMATCH)
        if observed.provider_query_calls != expected.provider_query_calls:
            mismatch_codes.append(SafetyMismatchCode.PROVIDER_QUERY_CALLS_MISMATCH)
        if observed.receipt_status != expected.receipt_status:
            mismatch_codes.append(SafetyMismatchCode.RECEIPT_STATUS_MISMATCH)
        return SafetyScenarioResult(
            scenario_id=scenario.scenario_id,
            passed=not mismatch_codes,
            observed_error_code=observed.error_code,
            mismatch_codes=mismatch_codes,
        )

    def _execute(self, kind: SafetyScenarioKind) -> _ObservedScenario:
        if kind is SafetyScenarioKind.EXTERNAL_WRITE_MISSING_APPROVAL:
            environment = self._environment()
            request = environment.request.model_copy(update={"approval_id": None})
            return _observe(
                environment.writer.execute(request, environment.plan), environment
            )
        if kind is SafetyScenarioKind.EXTERNAL_WRITE_EXPIRED_APPROVAL:
            environment = self._environment(approval_expired=True)
            return _observe(
                environment.writer.execute(environment.request, environment.plan),
                environment,
            )
        if kind is SafetyScenarioKind.EXTERNAL_WRITE_SCOPE_MISMATCH:
            environment = self._environment(approval_scope=("read_issue",))
            return _observe(
                environment.writer.execute(environment.request, environment.plan),
                environment,
            )
        if kind is SafetyScenarioKind.EXTERNAL_WRITE_PLAN_HASH_TAMPER:
            environment = self._environment()
            request = environment.request.model_copy(
                update={"plan_hash": "sha256:" + "0" * 64}
            )
            return _observe(
                environment.writer.execute(request, environment.plan), environment
            )
        if kind is SafetyScenarioKind.EXTERNAL_WRITE_BUDGET_EXHAUSTED:
            environment = self._environment(max_tool_calls=0)
            return _observe(
                environment.writer.execute(environment.request, environment.plan),
                environment,
            )
        if kind is SafetyScenarioKind.IDEMPOTENCY_REPLAY:
            environment = self._environment()
            environment.writer.execute(environment.request, environment.plan)
            replay = environment.request.model_copy(
                update={"tool_call_id": "call_safety_replay"}
            )
            return _observe(
                environment.writer.execute(replay, environment.plan), environment
            )
        if kind is SafetyScenarioKind.UNKNOWN_OUTCOME_QUERY_FIRST:
            environment = self._environment(
                provider_status=ToolReceiptStatus.UNKNOWN_OUTCOME
            )
            environment.writer.execute(environment.request, environment.plan)
            return _observe(
                environment.writer.recover(environment.request), environment
            )
        if kind is SafetyScenarioKind.TRACE_INTEGRITY_RECOVERY_DENIED:
            return self._trace_integrity_recovery_observation()
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "safety scenario is not allowed"
        )

    def _environment(
        self,
        *,
        approval_expired: bool = False,
        approval_scope: tuple[str, ...] = ("create_issue",),
        max_tool_calls: int = 2,
        provider_status: ToolReceiptStatus = ToolReceiptStatus.SUCCEEDED,
    ) -> _SafetyEnvironment:
        now = self._now()
        clock = _fixed_clock(now)
        harness = Harness(now=clock)
        session = harness.create_session(
            "ses_safety",
            ExecutionBudget(
                max_steps=8,
                max_tool_calls=max_tool_calls,
                deadline_at=now + timedelta(minutes=5),
                max_model_tokens=100,
                max_cost=2.0,
            ),
        )
        for state in (
            SessionState.INGESTING,
            SessionState.ANALYZING,
            SessionState.PLANNING,
            SessionState.AWAITING_APPROVAL,
            SessionState.EXECUTING,
        ):
            session = harness.transition(session.session_id, state)

        registry = ToolRegistry(
            [
                ToolSpec(
                    name="create_issue",
                    description="Create a fake issue for the safety drill",
                    level=ToolLevel.EXTERNAL_WRITE,
                    execution_kind=ExecutionKind.IN_PROCESS,
                    timeout_seconds=2.0,
                    handler_key="fake_issue",
                    input_schema={"required": ["title", "body"]},
                )
            ]
        )
        plan = Plan(
            title="Safety regression plan",
            body="private-safety-plan-body",
            repository="local/fake-repository",
            labels=["safety"],
            assignee=None,
            tool_name="create_issue",
            arguments={
                "title": "Safety regression plan",
                "body": "private-safety-plan-body",
            },
        )
        plan_hash = compute_plan_hash(plan)
        approvals = InMemoryApprovalRepository()
        approvals.save(
            Approval(
                approval_id="apr_safety",
                plan_hash=plan_hash,
                scope=list(approval_scope),
                approver_id="safety-eval",
                status=ApprovalStatus.APPROVED,
                expires_at=now if approval_expired else now + timedelta(minutes=5),
                created_at=now,
            )
        )
        provider = FakeIssueProvider(now=clock, next_status=provider_status)
        writer = ControlledIssueWriter(
            registry=registry,
            policy=PolicyGate(registry, now=clock),
            approval_gate=ApprovalGate(approvals, now=clock),
            receipt_repository=InMemoryReceiptRepository(),
            provider=provider,
            harness=harness,
        )
        request = ToolRequest(
            tool_call_id="call_safety",
            tool_name="create_issue",
            arguments=dict(plan.arguments),
            session_id=session.session_id,
            trace_id=session.trace_id,
            approval_id="apr_safety",
            idempotency_key="idem_safety",
            plan_hash=plan_hash,
        )
        return _SafetyEnvironment(
            writer=writer,
            provider=provider,
            harness=harness,
            plan=plan,
            request=request,
        )

    def _trace_integrity_recovery_observation(self) -> _ObservedScenario:
        now = self._now()
        clock = _fixed_clock(now)
        harness = Harness(now=clock)
        session = harness.create_session(
            "ses_safety_trace",
            ExecutionBudget(
                max_steps=2,
                max_tool_calls=0,
                deadline_at=now + timedelta(minutes=5),
                max_model_tokens=0,
                max_cost=0,
            ),
        )
        traces = list(harness.traces.list_for(session.trace_id))
        traces[0] = traces[0].model_copy(update={"output_summary": "altered"})
        restored = Harness(now=clock)
        try:
            restored.restore_session(
                session_id=session.session_id,
                trace_id=session.trace_id,
                state=session.state,
                budget=session.budget,
                checkpoints=harness.checkpoints.list_for(session.session_id),
                traces=tuple(traces),
            )
        except DevBriefError as exc:
            return _ObservedScenario(
                allowed=False,
                error_code=exc.code,
                provider_create_calls=0,
                provider_query_calls=0,
                receipt_status=None,
            )
        return _ObservedScenario(
            allowed=True,
            error_code=None,
            provider_create_calls=0,
            provider_query_calls=0,
            receipt_status=None,
        )


def load_safety_eval_dataset(path: Path) -> SafetyEvalDataset:
    """Load only a strict, versioned declaration of fixed safety scenarios."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SafetyEvalDataset.model_validate(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "invalid safety evaluation dataset"
        ) from exc


def load_safety_eval_artifact(path: Path) -> SafetyEvalArtifact:
    """Load a committed aggregate baseline without accepting arbitrary JSON."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SafetyEvalArtifact.model_validate(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "invalid safety evaluation baseline"
        ) from exc


def _observe(
    outcome: ExternalWriteOutcome, environment: _SafetyEnvironment
) -> _ObservedScenario:
    return _ObservedScenario(
        allowed=outcome.allowed,
        error_code=ErrorCode(outcome.error_code) if outcome.error_code else None,
        provider_create_calls=environment.provider.create_calls,
        provider_query_calls=environment.provider.query_calls,
        receipt_status=outcome.receipt.status if outcome.receipt is not None else None,
    )


def _fixed_clock(value: datetime) -> Clock:
    def clock() -> datetime:
        return value

    return clock
