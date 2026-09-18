from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from math import isfinite
from pathlib import PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from devbrief.domain.errors import ErrorCode


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SessionState(StrEnum):
    CREATED = "created"
    INGESTING = "ingesting"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED_RECOVERABLE = "failed_recoverable"
    FAILED_TERMINAL = "failed_terminal"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in {
            SessionState.COMPLETED,
            SessionState.CANCELLED,
            SessionState.FAILED_TERMINAL,
            SessionState.REJECTED,
            SessionState.EXPIRED,
        }


class CandidateKind(StrEnum):
    DECISION = "decision"
    ACTION_ITEM = "action_item"
    RISK = "risk"
    CLARIFICATION = "clarification"


class Resolution(StrEnum):
    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


class Priority(StrEnum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"
    UNKNOWN = "unknown"


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    NEEDS_CLARIFICATION = "needs_clarification"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class EvidenceKind(StrEnum):
    TRANSCRIPT_SEGMENT = "transcript_segment"
    AUDIO_RANGE = "audio_range"
    REPOSITORY_FILE = "repository_file"
    ADR = "adr"
    ISSUE = "issue"
    HUMAN_NOTE = "human_note"


class ContextSourceKind(StrEnum):
    SYSTEM_RULE = "system_rule"
    USER_INPUT = "user_input"
    TRANSCRIPT = "transcript"
    REPOSITORY_EVIDENCE = "repository_evidence"
    TOOL_RESULT = "tool_result"
    HUMAN_NOTE = "human_note"


class ContextTrustLevel(StrEnum):
    TRUSTED_POLICY = "trusted_policy"
    UNTRUSTED_DATA = "untrusted_data"


class RedactionState(StrEnum):
    REDACTED = "redacted"
    UNREDACTED = "unredacted"
    UNKNOWN = "unknown"


class Evidence(StrictModel):
    kind: EvidenceKind
    reference: str = Field(min_length=1)
    quote: str | None = None


class ContextSegment(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    source_kind: ContextSourceKind
    trust_level: ContextTrustLevel
    reference: str = Field(min_length=1)
    token_estimate: int = Field(gt=0)
    inclusion_reason: str = Field(min_length=1)
    redaction_state: RedactionState

    @property
    def source_id(self) -> str:
        """Compatibility name for application callers that use source identifiers."""
        return self.id


class ContextBuildMetadata(StrictModel):
    max_tokens: int = Field(gt=0)
    consumed_tokens: int = Field(ge=0)
    included: list[ContextSegment] = Field(
        default_factory=lambda: list[ContextSegment]()
    )
    excluded: list[ContextSegment] = Field(
        default_factory=lambda: list[ContextSegment]()
    )
    truncated_count: int = Field(ge=0)
    truncation_reasons: dict[str, str] = Field(default_factory=dict)


class TranscriptSegment(StrictModel):
    segment_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)

    @field_validator("end_ms")
    @classmethod
    def ends_after_start(cls, value: int, info: Any) -> int:
        start = info.data.get("start_ms")
        if start is not None and value <= start:
            raise ValueError("end_ms must be greater than start_ms")
        return value


class TranscriptFixture(StrictModel):
    fixture_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    fixture_version: str = Field(
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"
    )
    redacted: bool
    segments: list[TranscriptSegment] = Field(min_length=1)

    @field_validator("redacted")
    @classmethod
    def requires_redaction(cls, value: bool) -> bool:
        if not value:
            raise ValueError("transcript fixtures must be redacted")
        return value

    @field_validator("segments")
    @classmethod
    def ordered_segments(
        cls, value: list[TranscriptSegment]
    ) -> list[TranscriptSegment]:
        expected = sorted(
            value, key=lambda segment: (segment.start_ms, segment.segment_id)
        )
        if value != expected:
            raise ValueError("segments must be ordered by start_ms then segment_id")
        return value


class TranscriptEvidenceReference(StrictModel):
    reference: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @field_validator("end_ms")
    @classmethod
    def ends_after_start(cls, value: int, info: Any) -> int:
        start = info.data.get("start_ms")
        if start is not None and value <= start:
            raise ValueError("end_ms must be greater than start_ms")
        return value


class ImportedTranscript(StrictModel):
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    fixture_id: str = Field(min_length=1)
    fixture_version: str = Field(min_length=1)
    fixture_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    segment_count: int = Field(gt=0)
    evidence: list[TranscriptEvidenceReference] = Field(min_length=1)


class DecisionCandidate(StrictModel):
    candidate_id: str = Field(min_length=1)
    kind: CandidateKind
    statement: str = Field(min_length=1)
    owner: Resolution
    due_at: Resolution
    priority: Priority
    acceptance_criteria: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    status: CandidateStatus = CandidateStatus.PROPOSED


class EvalSample(StrictModel):
    sample_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    sample_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
    expected: list[DecisionCandidate] = Field(
        default_factory=lambda: list[DecisionCandidate]()
    )
    predicted: list[DecisionCandidate] = Field(
        default_factory=lambda: list[DecisionCandidate]()
    )


class EvalDatasetSample(StrictModel):
    """One public, labeled fixture evaluated by a local analyzer."""

    sample_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    fixture_path: str = Field(min_length=1)
    expected: list[DecisionCandidate] = Field(
        default_factory=lambda: list[DecisionCandidate]()
    )


class EvalDataset(StrictModel):
    """Versioned public inputs and labels for a reproducible Eval run."""

    dataset_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    dataset_version: str = Field(
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"
    )
    sample_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
    model_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    samples: list[EvalDatasetSample] = Field(min_length=1)


class EvalReport(StrictModel):
    sample_version: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    sample_count: int = Field(gt=0)
    candidate_precision: float = Field(ge=0, le=1)
    candidate_recall: float = Field(ge=0, le=1)
    candidate_f1: float = Field(ge=0, le=1)
    field_accuracy: dict[str, float] = Field(default_factory=dict)
    evidence_coverage: float = Field(ge=0, le=1)
    failure_counts: dict[str, int] = Field(default_factory=dict)


class EvalArtifact(StrictModel):
    """Redacted, reproducible aggregate output for one Eval dataset."""

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    report: EvalReport


class BenchmarkLatencySummary(StrictModel):
    """Aggregate monotonic wall-time measurements in milliseconds."""

    sample_count: int = Field(gt=0)
    min_ms: float = Field(ge=0)
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    max_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def _require_ordered_percentiles(self) -> Self:
        if any(
            not isfinite(value)
            for value in (self.min_ms, self.p50_ms, self.p95_ms, self.max_ms)
        ):
            raise ValueError("benchmark latency values must be finite")
        if not self.min_ms <= self.p50_ms <= self.p95_ms <= self.max_ms:
            raise ValueError("benchmark latency percentiles must be ordered")
        return self


class BenchmarkBudgetTotals(StrictModel):
    """Budget consumption aggregated only from successful workload results."""

    result_count: int = Field(ge=0)
    consumed_steps: int = Field(ge=0)
    consumed_tool_calls: int = Field(ge=0)
    consumed_model_tokens: int = Field(ge=0)
    consumed_cost: float = Field(ge=0)

    @field_validator("consumed_cost")
    @classmethod
    def _require_finite_cost(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("benchmark cost must be finite")
        return value


class BenchmarkReport(StrictModel):
    """Redacted aggregate outcomes for one measured benchmark workload."""

    latency: BenchmarkLatencySummary
    state_counts: dict[SessionState, int]
    error_counts: dict[ErrorCode, int]
    budget_totals: BenchmarkBudgetTotals

    @model_validator(mode="after")
    def _require_consistent_aggregate_counts(self) -> Self:
        state_total = sum(self.state_counts.values())
        error_total = sum(self.error_counts.values())
        if any(count < 0 for count in self.state_counts.values()):
            raise ValueError("benchmark state counts must be non-negative")
        if any(count < 0 for count in self.error_counts.values()):
            raise ValueError("benchmark error counts must be non-negative")
        if state_total != self.latency.sample_count:
            raise ValueError("benchmark state counts must cover every sample")
        if error_total > self.latency.sample_count:
            raise ValueError("benchmark error counts exceed the sample count")
        if self.state_counts.get(SessionState.FAILED_TERMINAL, 0) < error_total:
            raise ValueError("benchmark errors must map to failed terminal states")
        if self.budget_totals.result_count != self.latency.sample_count - error_total:
            raise ValueError("benchmark budget result count is inconsistent")
        return self


class BenchmarkArtifact(StrictModel):
    """Versioned, source-free benchmark output for a redacted fixture workload."""

    schema_version: int = Field(default=1, ge=1)
    workload_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    workload_version: str = Field(
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"
    )
    fixture_path: str = Field(min_length=1)
    fixture_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    warmup_iterations: int = Field(ge=0)
    measurement_iterations: int = Field(gt=0)
    report: BenchmarkReport

    @field_validator("fixture_path")
    @classmethod
    def _require_relative_posix_fixture_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if "\\" in value or value == "." or path.is_absolute() or ".." in path.parts:
            raise ValueError("benchmark fixture path must be a relative POSIX path")
        return path.as_posix()

    @model_validator(mode="after")
    def _require_report_sample_count(self) -> Self:
        if self.report.latency.sample_count != self.measurement_iterations:
            raise ValueError("benchmark latency sample count must match iterations")
        return self


class ExecutionBudget(StrictModel):
    max_steps: int = Field(gt=0)
    max_tool_calls: int = Field(ge=0)
    deadline_at: datetime
    max_model_tokens: int = Field(ge=0)
    max_cost: float = Field(ge=0)
    consumed_steps: int = Field(default=0, ge=0)
    consumed_tool_calls: int = Field(default=0, ge=0)
    consumed_model_tokens: int = Field(default=0, ge=0)
    consumed_cost: float = Field(default=0, ge=0)


class EventEnvelope(StrictModel):
    event_id: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)
    type: str = Field(min_length=1)
    occurred_at: datetime
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    causation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Checkpoint(StrictModel):
    checkpoint_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    state: SessionState
    schema_version: int = Field(default=1, ge=1)
    budget_summary: ExecutionBudget
    plan_hash: str | None = None
    approval_id: str | None = None
    completed_tool_call_ids: list[str] = Field(default_factory=list)
    idempotency_keys: list[str] = Field(default_factory=list)
    last_event_id: str | None = None
    trace_span_count: int | None = Field(default=None, ge=0)
    trace_head_hash: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    checkpoint_integrity_version: int | None = Field(default=None, ge=1)
    checkpoint_integrity_hash: str | None = Field(
        default=None, pattern=r"^sha256:[a-f0-9]{64}$"
    )
    redacted_context_refs: list[str] = Field(default_factory=list)
    created_at: datetime


class TraceKind(StrEnum):
    INPUT_INGEST = "input_ingest"
    STATE_TRANSITION = "state_transition"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    POLICY_CHECK = "policy_check"
    APPROVAL = "approval"
    CHECKPOINT = "checkpoint"


class TraceSpan(StrictModel):
    span_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    session_id: str = Field(min_length=1)
    kind: TraceKind
    input_summary: str = ""
    output_summary: str = ""
    elapsed_ms: int = Field(default=0, ge=0)
    error_code: str | None = None
    state_before: SessionState | None = None
    state_after: SessionState | None = None
    plan_hash: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_decision: str | None = None
    receipt_id: str | None = None
    integrity_version: int | None = Field(default=None, ge=1)
    sequence: int | None = Field(default=None, ge=1)
    previous_hash: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    integrity_hash: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")


class TraceIntegrityStatus(StrEnum):
    VERIFIED = "verified"
    LEGACY_UNSEALED = "legacy_unsealed"
    INVALID = "invalid"


class TraceIntegrityReport(StrictModel):
    """Redacted verification result for an ordered trace and checkpoint anchors."""

    status: TraceIntegrityStatus
    trace_id: str | None = None
    session_id: str | None = None
    span_count: int = Field(ge=0)
    anchored_checkpoint_count: int = Field(default=0, ge=0)
    mismatches: list[str] = Field(default_factory=list)


_SAFE_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class RuntimeMetricsBudgetTotals(StrictModel):
    """Aggregate budget consumption without retaining per-session details."""

    session_count: int = Field(ge=0)
    consumed_steps: int = Field(ge=0)
    consumed_tool_calls: int = Field(ge=0)
    consumed_model_tokens: int = Field(ge=0)
    consumed_cost: float = Field(ge=0)

    @field_validator("consumed_cost")
    @classmethod
    def _require_finite_cost(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("runtime metrics cost must be finite")
        return value


class RuntimeMetricsSnapshot(StrictModel):
    """Versioned aggregate-only observability output for persisted runs."""

    schema_version: Literal[1] = 1
    session_count: int = Field(ge=0)
    state_counts: dict[SessionState, int] = Field(
        default_factory=lambda: dict[SessionState, int]()
    )
    error_counts: dict[ErrorCode, int] = Field(
        default_factory=lambda: dict[ErrorCode, int]()
    )
    trace_kind_counts: dict[TraceKind, int] = Field(
        default_factory=lambda: dict[TraceKind, int]()
    )
    tool_counts: dict[str, int] = Field(default_factory=lambda: dict[str, int]())
    span_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    checkpoint_count: int = Field(ge=0)
    recover_count: int = Field(ge=0)
    trace_integrity_counts: dict[TraceIntegrityStatus, int] = Field(
        default_factory=lambda: dict[TraceIntegrityStatus, int]()
    )
    trace_integrity_mismatch_counts: dict[str, int] = Field(
        default_factory=lambda: dict[str, int]()
    )
    budget_totals: RuntimeMetricsBudgetTotals

    @model_validator(mode="after")
    def _require_consistent_counts(self) -> Self:
        count_maps = (
            self.state_counts,
            self.error_counts,
            self.trace_kind_counts,
            self.tool_counts,
            self.trace_integrity_counts,
            self.trace_integrity_mismatch_counts,
        )
        if any(count < 0 for mapping in count_maps for count in mapping.values()):
            raise ValueError("runtime metrics counts must be non-negative")
        if self.budget_totals.session_count != self.session_count:
            raise ValueError("runtime metrics budget count is inconsistent")
        if sum(self.state_counts.values()) != self.session_count:
            raise ValueError("runtime metrics state counts must cover every session")
        if sum(self.trace_integrity_counts.values()) != self.session_count:
            raise ValueError(
                "runtime metrics integrity counts must cover every session"
            )
        if self.model_call_count != self.trace_kind_counts.get(TraceKind.MODEL_CALL, 0):
            raise ValueError("runtime metrics model call count is inconsistent")
        if self.tool_call_count != self.trace_kind_counts.get(TraceKind.TOOL_CALL, 0):
            raise ValueError("runtime metrics tool call count is inconsistent")
        if sum(self.trace_kind_counts.values()) != self.span_count:
            raise ValueError("runtime metrics span count is inconsistent")
        return self

    @field_validator("tool_counts")
    @classmethod
    def _require_safe_tool_names(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not _SAFE_METRIC_NAME.fullmatch(name) for name in value):
            raise ValueError("runtime metrics tool names must be safe identifiers")
        return value

    @field_validator("trace_integrity_mismatch_counts")
    @classmethod
    def _require_safe_mismatch_names(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not _SAFE_METRIC_NAME.fullmatch(name) for name in value):
            raise ValueError("runtime metrics mismatch names must be safe identifiers")
        return value

    @property
    def integrity_status_counts(self) -> dict[TraceIntegrityStatus, int]:
        """Compatibility alias for callers that use the shorter metric name."""
        return self.trace_integrity_counts

    @property
    def integrity_mismatch_counts(self) -> dict[str, int]:
        """Compatibility alias for callers that use the shorter metric name."""
        return self.trace_integrity_mismatch_counts


class TraceReplayReport(StrictModel):
    replayable: bool
    trace_id: str | None = None
    session_id: str | None = None
    final_state: SessionState | None = None
    state_sequence: list[SessionState] = Field(
        default_factory=lambda: list[SessionState]()
    )
    plan_hashes: list[str] = Field(default_factory=list)
    tool_decisions: list[str] = Field(default_factory=list)
    receipt_refs: list[str] = Field(default_factory=list)
    integrity_status: TraceIntegrityStatus = TraceIntegrityStatus.LEGACY_UNSEALED
    integrity_mismatches: list[str] = Field(default_factory=list)
    mismatches: list[str] = Field(default_factory=list)


class ToolLevel(StrEnum):
    READ = "read"
    DRAFT_WRITE = "draft_write"
    EXTERNAL_WRITE = "external_write"
    PROHIBITED_IN_MVP = "prohibited_in_mvp"


class ExecutionKind(StrEnum):
    IN_PROCESS = "in_process"
    HTTP_ADAPTER = "http_adapter"
    REMOTE_MCP = "remote_mcp"
    MANUAL = "manual"


class ToolSpec(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    level: ToolLevel
    execution_kind: ExecutionKind
    timeout_seconds: float = Field(gt=0)
    handler_key: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolRequest(StrictModel):
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(min_length=1)
    trace_id: str | None = Field(default=None, min_length=1)
    approval_id: str | None = None
    idempotency_key: str | None = None
    plan_hash: str | None = None


class ToolPolicyDecision(StrictModel):
    allowed: bool
    tool_name: str = Field(min_length=1)
    tool_level: ToolLevel
    error_code: str | None = None
    reason: str = Field(min_length=1)
    budget_after: ExecutionBudget | None = None


class ToolResult(StrictModel):
    summary: str = Field(min_length=1)
    references: list[str] = Field(default_factory=list)


class ToolDispatchOutcome(StrictModel):
    allowed: bool
    tool_name: str = Field(min_length=1)
    result: ToolResult | None = None
    error_code: str | None = None
    reason: str = Field(min_length=1)


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class Approval(StrictModel):
    approval_id: str = Field(min_length=1)
    plan_hash: str = Field(min_length=1)
    scope: list[str] = Field(min_length=1)
    approver_id: str = Field(min_length=1)
    status: ApprovalStatus
    expires_at: datetime
    created_at: datetime


class ApprovalDecision(StrictModel):
    allowed: bool
    approval_id: str = Field(min_length=1)
    plan_hash: str = Field(min_length=1)
    status: ApprovalStatus | None = None
    error_code: str | None = None
    reason: str = Field(min_length=1)


class ToolReceiptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    UNKNOWN_OUTCOME = "unknown_outcome"


class ToolReceipt(StrictModel):
    receipt_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ToolReceiptStatus
    external_object_id: str | None = None
    external_url: str | None = None
    provider_request_id: str | None = None
    idempotency_key: str = Field(min_length=1)
    created_at: datetime


class ExternalWriteOutcome(StrictModel):
    allowed: bool
    tool_name: str = Field(min_length=1)
    receipt: ToolReceipt | None = None
    error_code: str | None = None
    reason: str = Field(min_length=1)


class SafetyScenarioKind(StrEnum):
    EXTERNAL_WRITE_MISSING_APPROVAL = "external_write_missing_approval"
    EXTERNAL_WRITE_EXPIRED_APPROVAL = "external_write_expired_approval"
    EXTERNAL_WRITE_SCOPE_MISMATCH = "external_write_scope_mismatch"
    EXTERNAL_WRITE_PLAN_HASH_TAMPER = "external_write_plan_hash_tamper"
    EXTERNAL_WRITE_BUDGET_EXHAUSTED = "external_write_budget_exhausted"
    IDEMPOTENCY_REPLAY = "idempotency_replay"
    UNKNOWN_OUTCOME_QUERY_FIRST = "unknown_outcome_query_first"
    TRACE_INTEGRITY_RECOVERY_DENIED = "trace_integrity_recovery_denied"


class SafetyMismatchCode(StrEnum):
    ALLOWED_MISMATCH = "allowed_mismatch"
    ERROR_CODE_MISMATCH = "error_code_mismatch"
    PROVIDER_CREATE_CALLS_MISMATCH = "provider_create_calls_mismatch"
    PROVIDER_QUERY_CALLS_MISMATCH = "provider_query_calls_mismatch"
    RECEIPT_STATUS_MISMATCH = "receipt_status_mismatch"


class SafetyScenarioExpectation(StrictModel):
    """Fixed, source-free assertions for one fake Harness safety scenario."""

    allowed: bool
    error_code: ErrorCode | None = None
    provider_create_calls: int = Field(ge=0)
    provider_query_calls: int = Field(ge=0)
    receipt_status: ToolReceiptStatus | None = None

    @model_validator(mode="after")
    def _require_error_code_for_denial(self) -> Self:
        if self.allowed and self.error_code is not None:
            raise ValueError("allowed safety scenarios cannot carry an error code")
        if not self.allowed and self.error_code is None:
            raise ValueError("denied safety scenarios require an error code")
        return self


class SafetyEvalScenario(StrictModel):
    """One allowlisted scenario declaration; it cannot contain executable input."""

    scenario_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    scenario_version: str = Field(
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"
    )
    kind: SafetyScenarioKind
    expected: SafetyScenarioExpectation


class SafetyEvalDataset(StrictModel):
    """Versioned, no-credential declarations for fixed Harness safety drills."""

    dataset_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    dataset_version: str = Field(
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"
    )
    scenarios: list[SafetyEvalScenario] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_unique_ids_and_one_scenario_version(self) -> Self:
        scenario_ids = [item.scenario_id for item in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("safety scenario ids must be unique")
        versions = {item.scenario_version for item in self.scenarios}
        if len(versions) != 1:
            raise ValueError("safety scenarios must use one scenario version")
        return self


class SafetyScenarioResult(StrictModel):
    """Redacted result containing only an ID, stable error enum and mismatch codes."""

    scenario_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    passed: bool
    observed_error_code: ErrorCode | None = None
    mismatch_codes: list[SafetyMismatchCode] = Field(
        default_factory=lambda: list[SafetyMismatchCode]()
    )

    @model_validator(mode="after")
    def _require_consistent_mismatches(self) -> Self:
        if len(self.mismatch_codes) != len(set(self.mismatch_codes)):
            raise ValueError("safety mismatch codes must be unique per scenario")
        if self.passed and self.mismatch_codes:
            raise ValueError("passing safety scenarios cannot contain mismatches")
        if not self.passed and not self.mismatch_codes:
            raise ValueError("failing safety scenarios require mismatch codes")
        return self


class SafetyEvalReport(StrictModel):
    """Aggregate safety evidence without plan, receipt or trace source content."""

    scenario_version: str = Field(min_length=1)
    scenario_count: int = Field(gt=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    failure_counts: dict[SafetyMismatchCode, int] = Field(
        default_factory=lambda: dict[SafetyMismatchCode, int]()
    )
    results: list[SafetyScenarioResult] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_consistent_results(self) -> Self:
        if len(self.results) != self.scenario_count:
            raise ValueError("safety result count must match scenario count")
        scenario_ids = [item.scenario_id for item in self.results]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("safety result ids must be unique")
        passed_count = sum(item.passed for item in self.results)
        if passed_count != self.passed_count:
            raise ValueError("safety passed count is inconsistent")
        if self.failed_count != self.scenario_count - passed_count:
            raise ValueError("safety failed count is inconsistent")
        if any(count < 0 for count in self.failure_counts.values()):
            raise ValueError("safety failure counts must be non-negative")
        observed_counts: dict[SafetyMismatchCode, int] = {}
        for result in self.results:
            for code in result.mismatch_codes:
                observed_counts[code] = observed_counts.get(code, 0) + 1
        if self.failure_counts != observed_counts:
            raise ValueError("safety failure counts must match scenario mismatches")
        return self


class SafetyEvalArtifact(StrictModel):
    """Versioned, aggregate-only output from the deterministic safety runner."""

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    runner_version: str = Field(min_length=1)
    report: SafetyEvalReport


class Plan(StrictModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)
    assignee: str | None = None
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class SimilarIssue(StrictModel):
    issue_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)
    reference: str = Field(min_length=1)
    similarity: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=240)
    review_required: bool = True


class TaskDraft(StrictModel):
    plan: Plan
    plan_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    evidence_refs: list[str] = Field(min_length=1)
    clarification_items: list[str] = Field(default_factory=list)
    similar_issues: list[SimilarIssue] = Field(
        default_factory=lambda: list[SimilarIssue]()
    )


class TriageRunResult(StrictModel):
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    state: SessionState
    candidates: list[DecisionCandidate] = Field(
        default_factory=lambda: list[DecisionCandidate]()
    )
    draft: TaskDraft
    approval_status: ApprovalStatus
    budget: ExecutionBudget
    approval_id: str | None = None
    receipt: ToolReceipt | None = None
    error: str | None = None
