from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from devbrief.domain.approval import compute_plan_hash
from devbrief.domain.contracts import (
    CandidateKind,
    DecisionCandidate,
    Plan,
    Priority,
    Resolution,
    SimilarIssue,
    TaskDraft,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.trace import redact_summary


class FakeTaskPlanner:
    """Create a local, evidence-linked task draft without external side effects."""

    def __init__(self, *, repository: str = "local/fake-repository") -> None:
        self._repository = repository

    def create_draft(
        self,
        candidates: Sequence[DecisionCandidate],
        *,
        similar_issues: Sequence[Mapping[str, object] | SimilarIssue] = (),
    ) -> TaskDraft:
        """Build one deterministic plan and reject candidates without evidence."""
        try:
            candidate = _select_candidate(candidates)
            evidence_refs = _evidence_refs(candidate)
            clarifications = _clarifications(candidate)
            plan = Plan(
                title=_title(candidate),
                body=_body(candidate, evidence_refs, clarifications),
                repository=self._repository,
                labels=_labels(candidate),
                assignee=None,
                tool_name="create_issue_draft",
                arguments={
                    "candidate_id": candidate.candidate_id,
                    "evidence_refs": evidence_refs,
                },
            )
            normalized_similar = _normalize_similar_issues(similar_issues)
            return TaskDraft(
                plan=plan,
                plan_hash=compute_plan_hash(plan),
                evidence_refs=evidence_refs,
                clarification_items=clarifications,
                similar_issues=normalized_similar,
            )
        except (TypeError, ValidationError, ValueError) as exc:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "cannot create task draft"
            ) from exc


def _select_candidate(candidates: Sequence[DecisionCandidate]) -> DecisionCandidate:
    if not candidates:
        raise ValueError("at least one candidate is required")
    for candidate in candidates:
        if candidate.kind in {CandidateKind.ACTION_ITEM, CandidateKind.DECISION}:
            if not candidate.evidence:
                raise ValueError("candidate evidence is required")
            return candidate
    raise ValueError("no plan-compatible candidate")


def _evidence_refs(candidate: DecisionCandidate) -> list[str]:
    references = sorted({item.reference for item in candidate.evidence})
    if not references:
        raise ValueError("candidate evidence is required")
    return references


def _clarifications(candidate: DecisionCandidate) -> list[str]:
    result: list[str] = []
    if candidate.owner is not Resolution.RESOLVED:
        result.append("owner")
    if candidate.due_at is not Resolution.RESOLVED:
        result.append("due_at")
    return result


def _title(candidate: DecisionCandidate) -> str:
    return redact_summary(f"[Bug Triage] {candidate.statement}", limit=120)


def _body(
    candidate: DecisionCandidate,
    evidence_refs: list[str],
    clarifications: list[str],
) -> str:
    lines = [
        "## 背景",
        redact_summary(candidate.statement),
        "",
        "## 优先级",
        candidate.priority.value.upper(),
        "",
        "负责人："
        + ("待确认" if candidate.owner is not Resolution.RESOLVED else "已确认"),
        "",
        "期限："
        + ("待确认" if candidate.due_at is not Resolution.RESOLVED else "已确认"),
        "",
        "## 验收条件",
    ]
    lines.extend(f"- {redact_summary(item)}" for item in candidate.acceptance_criteria)
    lines.extend(("", "## 证据引用"))
    lines.extend(f"- {reference}" for reference in evidence_refs)
    if clarifications:
        lines.extend(("", "## 待澄清", "- " + "、".join(clarifications)))
    return "\n".join(lines)


def _labels(candidate: DecisionCandidate) -> list[str]:
    labels = ["bug"]
    if candidate.priority is not Priority.UNKNOWN:
        labels.append(f"priority-{candidate.priority.value}")
    return labels


def _normalize_similar_issues(
    items: Sequence[Mapping[str, object] | SimilarIssue],
) -> list[SimilarIssue]:
    normalized = [
        item if isinstance(item, SimilarIssue) else SimilarIssue.model_validate(item)
        for item in items
    ]
    return sorted(
        normalized,
        key=lambda item: (-item.similarity, item.issue_id, item.reference),
    )
