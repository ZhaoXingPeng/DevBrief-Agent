from __future__ import annotations

from collections.abc import Sequence

from devbrief.domain.contracts import DecisionCandidate, EvalReport, EvalSample
from devbrief.domain.errors import DevBriefError, ErrorCode

_FAILURE_CODES = (
    "missing_candidate",
    "unexpected_candidate",
    "duplicate_candidate_id",
    "field_mismatch",
    "no_evidence",
)
_FIELD_NAMES = (
    "kind",
    "statement",
    "owner",
    "due_at",
    "priority",
    "acceptance_criteria",
    "status",
)


class EvalRunner:
    """Run deterministic candidate metrics over a versioned sample set."""

    def run(
        self,
        samples: Sequence[EvalSample],
        *,
        model_version: str,
        prompt_version: str,
    ) -> EvalReport:
        if not samples or not model_version.strip() or not prompt_version.strip():
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "samples, model version and prompt version are required",
            )
        sample_versions = {sample.sample_version for sample in samples}
        if len(sample_versions) != 1:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "all eval samples must use one sample version",
            )
        sample_ids = [sample.sample_id for sample in samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "eval sample ids must be unique",
            )

        failures = {code: 0 for code in _FAILURE_CODES}
        field_correct = {field: 0 for field in _FIELD_NAMES}
        field_total = {field: 0 for field in _FIELD_NAMES}
        predicted_count = 0
        expected_count = 0
        matched_count = 0
        evidence_count = 0

        for sample in samples:
            expected_by_id, expected_duplicates = _index_candidates(sample.expected)
            predicted_by_id, predicted_duplicates = _index_candidates(sample.predicted)
            failures["duplicate_candidate_id"] += (
                expected_duplicates + predicted_duplicates
            )
            expected_count += len(sample.expected)
            predicted_count += len(sample.predicted)
            evidence_count += sum(bool(item.evidence) for item in sample.predicted)
            matched_ids = set(expected_by_id) & set(predicted_by_id)
            matched_count += len(matched_ids)

            failures["missing_candidate"] += len(
                set(expected_by_id) - set(predicted_by_id)
            )
            failures["unexpected_candidate"] += len(
                set(predicted_by_id) - set(expected_by_id)
            )
            failures["no_evidence"] += sum(
                not item.evidence for item in sample.predicted
            )
            for candidate_id in matched_ids:
                expected = expected_by_id[candidate_id]
                predicted = predicted_by_id[candidate_id]
                mismatch = False
                for field in _FIELD_NAMES:
                    field_total[field] += 1
                    if getattr(expected, field) == getattr(predicted, field):
                        field_correct[field] += 1
                    else:
                        mismatch = True
                if mismatch:
                    failures["field_mismatch"] += 1

        precision = _ratio(matched_count, predicted_count)
        recall = _ratio(matched_count, expected_count)
        f1 = _f1(precision, recall)
        field_accuracy = {
            field: _ratio(field_correct[field], field_total[field])
            for field in _FIELD_NAMES
        }
        return EvalReport(
            sample_version=next(iter(sample_versions)),
            model_version=model_version,
            prompt_version=prompt_version,
            sample_count=len(samples),
            candidate_precision=precision,
            candidate_recall=recall,
            candidate_f1=f1,
            field_accuracy=field_accuracy,
            evidence_coverage=_ratio(evidence_count, predicted_count),
            failure_counts=failures,
        )


def _index_candidates(
    candidates: Sequence[DecisionCandidate],
) -> tuple[dict[str, DecisionCandidate], int]:
    indexed: dict[str, DecisionCandidate] = {}
    duplicates = 0
    for candidate in candidates:
        if candidate.candidate_id in indexed:
            duplicates += 1
            continue
        indexed[candidate.candidate_id] = candidate
    return indexed, duplicates


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0 if numerator == 0 else 0.0
    return numerator / denominator


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
