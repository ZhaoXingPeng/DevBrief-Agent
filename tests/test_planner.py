from __future__ import annotations

import json

import pytest

from devbrief.application.planner import FakeTaskPlanner
from devbrief.domain.contracts import (
    CandidateKind,
    DecisionCandidate,
    Evidence,
    EvidenceKind,
    Priority,
    Resolution,
)
from devbrief.domain.errors import DevBriefError, ErrorCode


def candidate(
    *,
    statement: str = "Track remediation for the authentication refresh failure.",
    priority: Priority = Priority.UNKNOWN,
    quote: str | None = None,
) -> DecisionCandidate:
    return DecisionCandidate(
        candidate_id="can_auth_1",
        kind=CandidateKind.ACTION_ITEM,
        statement=statement,
        owner=Resolution.UNKNOWN,
        due_at=Resolution.UNKNOWN,
        priority=priority,
        acceptance_criteria=["Confirm a remediation owner."],
        evidence=[
            Evidence(
                kind=EvidenceKind.TRANSCRIPT_SEGMENT,
                reference="fixture://bug-triage-redacted-v1/1.0.0#seg_001",
                quote=quote,
            )
        ],
        confidence=1.0,
    )


def test_planner_builds_stable_plan_with_evidence_and_clarifications() -> None:
    planner = FakeTaskPlanner()
    first = planner.create_draft([candidate()])
    second = planner.create_draft([candidate()])

    assert first == second
    assert first.plan_hash.startswith("sha256:")
    assert first.plan.tool_name == "create_issue_draft"
    assert first.plan.assignee is None
    assert first.evidence_refs == ["fixture://bug-triage-redacted-v1/1.0.0#seg_001"]
    assert first.clarification_items == ["owner", "due_at"]
    assert "负责人：待确认" in first.plan.body
    assert "期限：待确认" in first.plan.body
    assert "seg_001" in first.plan.body


def test_planner_preserves_explicit_priority_without_inventing_assignment() -> None:
    draft = FakeTaskPlanner().create_draft([candidate(priority=Priority.P0)])

    assert draft.plan.labels == ["bug", "priority-p0"]
    assert draft.plan.assignee is None
    assert "P0" in draft.plan.body


def test_planner_reports_possible_similarity_for_human_review_only() -> None:
    draft = FakeTaskPlanner().create_draft(
        [candidate()],
        similar_issues=[
            {
                "issue_id": "42",
                "title": "Refresh token timeout investigation",
                "reference": "issue://org/repo/42",
                "similarity": 0.91,
                "rationale": "Both mention authentication refresh failure.",
            },
            {
                "issue_id": "7",
                "title": "Unrelated issue",
                "reference": "issue://org/repo/7",
                "similarity": 0.2,
                "rationale": "Low lexical overlap.",
            },
        ],
    )

    assert [item.issue_id for item in draft.similar_issues] == ["42", "7"]
    assert all(item.review_required for item in draft.similar_issues)
    assert "确定重复" not in json.dumps(
        draft.model_dump(mode="json"), ensure_ascii=False
    )


def test_planner_never_copies_transcript_quote_or_external_body() -> None:
    private_text = "Private transcript token=private-value"
    draft = FakeTaskPlanner().create_draft([candidate(quote=private_text)])

    serialized = json.dumps(draft.model_dump(mode="json"), ensure_ascii=False)
    assert private_text not in serialized
    assert "private-value" not in serialized
    assert "quote" not in draft.plan.body


def test_planner_rejects_missing_evidence_and_empty_candidates() -> None:
    no_evidence = candidate().model_copy(update={"evidence": []})

    for candidates in ([], [no_evidence]):
        with pytest.raises(DevBriefError) as raised:
            FakeTaskPlanner().create_draft(candidates)
        assert raised.value.code is ErrorCode.VALIDATION_ERROR


def test_planner_hash_changes_when_any_plan_bound_field_changes() -> None:
    original = FakeTaskPlanner().create_draft([candidate()])
    changed = FakeTaskPlanner().create_draft(
        [candidate(statement="Track remediation and add a regression test.")]
    )

    assert original.plan_hash != changed.plan_hash
