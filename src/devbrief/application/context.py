from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from devbrief.domain.contracts import (
    ContextBuildMetadata,
    ContextSegment,
    ContextSourceKind,
    ContextTrustLevel,
    RedactionState,
)
from devbrief.domain.errors import DevBriefError, ErrorCode


class ContextInput(BaseModel):
    """Transient context content supplied by an application or Adapter boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: str
    source_kind: ContextSourceKind
    reference: str
    content: str
    token_estimate: int
    trust_level: ContextTrustLevel = ContextTrustLevel.UNTRUSTED_DATA
    redaction_state: RedactionState = RedactionState.UNKNOWN

    @field_validator("source_id", "reference", "content")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value:
            raise ValueError("context text fields must not be empty")
        return value


class ContextBuildResult(BaseModel):
    """Short-lived prompt plus metadata safe to persist in a trace or checkpoint."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    metadata: ContextBuildMetadata


_SOURCE_PRIORITY: dict[ContextSourceKind, int] = {
    ContextSourceKind.SYSTEM_RULE: 0,
    ContextSourceKind.TRANSCRIPT: 1,
    ContextSourceKind.USER_INPUT: 2,
    ContextSourceKind.REPOSITORY_EVIDENCE: 3,
    ContextSourceKind.TOOL_RESULT: 4,
    ContextSourceKind.HUMAN_NOTE: 5,
}


class ContextBuilder:
    """Build deterministic, source-labelled context under an explicit token budget."""

    def build(
        self, inputs: list[ContextInput], *, max_tokens: int
    ) -> ContextBuildResult:
        if max_tokens <= 0:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "context max_tokens must be positive"
            )
        try:
            normalized = [_normalize(item) for item in inputs]
        except (TypeError, ValidationError, ValueError) as exc:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "invalid context input"
            ) from exc

        ordered = sorted(
            normalized,
            key=lambda item: (
                _SOURCE_PRIORITY[item.source_kind],
                item.source_id,
                item.reference,
            ),
        )
        included: list[tuple[ContextSegment, str]] = []
        excluded: list[ContextSegment] = []
        consumed = 0
        for item in ordered:
            if consumed + item.token_estimate <= max_tokens:
                reason = _inclusion_reason(item.source_kind)
                included.append((_metadata(item, reason), item.content))
                consumed += item.token_estimate
            else:
                excluded.append(_metadata(item, "context_budget_exceeded"))

        metadata = ContextBuildMetadata(
            max_tokens=max_tokens,
            consumed_tokens=consumed,
            included=[item for item, _ in included],
            excluded=excluded,
            truncated_count=len(excluded),
            truncation_reasons={
                item.id: "context_budget_exceeded" for item in excluded
            },
        )
        return ContextBuildResult(
            prompt=_render_prompt(included),
            metadata=metadata,
        )


def _normalize(item: ContextInput) -> ContextInput:
    if item.token_estimate <= 0:
        raise ValueError("context token_estimate must be positive")
    return item


def _metadata(item: ContextInput, reason: str) -> ContextSegment:
    trust = (
        ContextTrustLevel.TRUSTED_POLICY
        if item.source_kind is ContextSourceKind.SYSTEM_RULE
        else ContextTrustLevel.UNTRUSTED_DATA
    )
    return ContextSegment(
        id=item.source_id,
        source_kind=item.source_kind,
        trust_level=trust,
        reference=item.reference,
        token_estimate=item.token_estimate,
        inclusion_reason=reason,
        redaction_state=item.redaction_state,
    )


def _inclusion_reason(kind: ContextSourceKind) -> str:
    if kind is ContextSourceKind.SYSTEM_RULE:
        return "required_system_policy"
    if kind is ContextSourceKind.TRANSCRIPT:
        return "transcript_evidence_priority"
    return "source_priority_order"


def _render_prompt(included: list[tuple[ContextSegment, str]]) -> str:
    sections: list[str] = []
    for metadata, content in included:
        sections.append(
            "\n".join(
                (
                    f"[context id={metadata.id} source={metadata.source_kind.value} "
                    f"trust={metadata.trust_level.value} "
                    f"reference={metadata.reference}]",
                    content,
                    "[/context]",
                )
            )
        )
    return "\n\n".join(sections)
