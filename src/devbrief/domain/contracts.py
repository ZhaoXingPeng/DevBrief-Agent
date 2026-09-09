from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class Evidence(StrictModel):
    kind: EvidenceKind
    reference: str = Field(min_length=1)
    quote: str | None = None


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


class Plan(StrictModel):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)
    assignee: str | None = None
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
