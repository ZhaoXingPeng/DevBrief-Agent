from __future__ import annotations

import pytest

from devbrief.domain.contracts import (
    CandidateKind,
    CandidateStatus,
    DecisionCandidate,
    EvalSample,
    Evidence,
    EvidenceKind,
    Priority,
    Resolution,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.eval import EvalRunner


def candidate(
    candidate_id: str,
    *,
    statement: str = "Fix authentication refresh",
    owner: Resolution = Resolution.UNKNOWN,
    due_at: Resolution = Resolution.UNKNOWN,
    priority: Priority = Priority.P1,
    evidence: list[Evidence] | None = None,
    status: CandidateStatus = CandidateStatus.PROPOSED,
) -> DecisionCandidate:
    return DecisionCandidate(
        candidate_id=candidate_id,
        kind=CandidateKind.ACTION_ITEM,
        statement=statement,
        owner=owner,
        due_at=due_at,
        priority=priority,
        acceptance_criteria=["Regression test passes"],
        evidence=(
            [
                Evidence(
                    kind=EvidenceKind.TRANSCRIPT_SEGMENT,
                    reference=f"fixture://sample#{candidate_id}",
                )
            ]
            if evidence is None
            else evidence
        ),
        confidence=0.8,
        status=status,
    )


def sample(
    sample_id: str,
    expected: list[DecisionCandidate],
    predicted: list[DecisionCandidate],
    *,
    version: str = "1.0.0",
) -> EvalSample:
    return EvalSample(
        sample_id=sample_id,
        sample_version=version,
        expected=expected,
        predicted=predicted,
    )


def test_eval_reports_deterministic_perfect_match_without_candidate_text() -> None:
    item = candidate("c1", statement="Private synthetic candidate text")
    runner = EvalRunner()
    current = sample("bug_001", [item], [item])

    first = runner.run([current], model_version="fake-model-1", prompt_version="p1")
    second = runner.run([current], model_version="fake-model-1", prompt_version="p1")

    assert first == second
    assert first.sample_version == "1.0.0"
    assert first.sample_count == 1
    assert first.candidate_precision == 1
    assert first.candidate_recall == 1
    assert first.candidate_f1 == 1
    assert first.evidence_coverage == 1
    assert all(value == 1 for value in first.field_accuracy.values())
    assert first.failure_counts == {
        "missing_candidate": 0,
        "unexpected_candidate": 0,
        "duplicate_candidate_id": 0,
        "field_mismatch": 0,
        "no_evidence": 0,
    }
    assert "Private synthetic candidate text" not in first.model_dump_json()


def test_eval_classifies_missing_unexpected_duplicate_field_and_evidence_failures() -> (
    None
):
    expected = [candidate("c1"), candidate("c2")]
    predicted = [
        candidate("c1", owner=Resolution.RESOLVED),
        candidate("c1").model_copy(update={"evidence": []}),
        candidate("c3"),
    ]
    report = EvalRunner().run(
        [sample("bug_002", expected, predicted)],
        model_version="fake-model-1",
        prompt_version="p1",
    )

    assert abs(report.candidate_precision - 1 / 3) < 1e-9
    assert abs(report.candidate_recall - 1 / 2) < 1e-9
    assert abs(report.candidate_f1 - 2 / 5) < 1e-9
    assert abs(report.evidence_coverage - 2 / 3) < 1e-9
    assert report.failure_counts["missing_candidate"] == 1
    assert report.failure_counts["unexpected_candidate"] == 1
    assert report.failure_counts["duplicate_candidate_id"] == 1
    assert report.failure_counts["field_mismatch"] == 1
    assert report.failure_counts["no_evidence"] == 1
    assert report.field_accuracy["owner"] == 0


def test_eval_rejects_empty_or_mixed_version_samples() -> None:
    runner = EvalRunner()
    with pytest.raises(DevBriefError) as empty:
        runner.run([], model_version="fake", prompt_version="p1")
    assert empty.value.code is ErrorCode.VALIDATION_ERROR

    with pytest.raises(DevBriefError) as mixed:
        runner.run(
            [
                sample("bug_001", [], [], version="1.0.0"),
                sample("bug_002", [], [], version="2.0.0"),
            ],
            model_version="fake",
            prompt_version="p1",
        )
    assert mixed.value.code is ErrorCode.VALIDATION_ERROR


def test_eval_rejects_empty_model_or_prompt_version() -> None:
    current = sample("bug_001", [], [])
    runner = EvalRunner()

    for model_version, prompt_version in (("", "p1"), ("fake", "")):
        with pytest.raises(DevBriefError) as raised:
            runner.run(
                [current],
                model_version=model_version,
                prompt_version=prompt_version,
            )
        assert raised.value.code is ErrorCode.VALIDATION_ERROR
