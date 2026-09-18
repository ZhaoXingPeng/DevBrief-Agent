from __future__ import annotations

import json
from collections.abc import Sequence
from hashlib import sha256

from devbrief.domain.contracts import (
    Checkpoint,
    TraceIntegrityReport,
    TraceIntegrityStatus,
    TraceSpan,
)

_INTEGRITY_VERSION = 1
_HASH_PREFIX = "sha256:"
_INTEGRITY_FIELDS = {
    "integrity_version",
    "sequence",
    "previous_hash",
    "integrity_hash",
}
_CHECKPOINT_INTEGRITY_FIELDS = {
    "checkpoint_integrity_version",
    "checkpoint_integrity_hash",
}


def seal_trace_span(
    span: TraceSpan, *, sequence: int, previous_hash: str | None
) -> TraceSpan:
    """Return a new span linked to its verified predecessor.

    The digest covers only already-redacted trace fields plus the causal sequence.
    It is tamper-evident within the local application boundary, not a signed audit log.
    """
    if sequence < 1:
        raise ValueError("trace sequence must start at one")
    if _integrity_mode(span) != "unsealed":
        raise ValueError("trace span already has integrity metadata")
    integrity_hash = _digest(span, sequence=sequence, previous_hash=previous_hash)
    return span.model_copy(
        update={
            "integrity_version": _INTEGRITY_VERSION,
            "sequence": sequence,
            "previous_hash": previous_hash,
            "integrity_hash": integrity_hash,
        }
    )


def append_trace_span(spans: Sequence[TraceSpan], span: TraceSpan) -> TraceSpan:
    """Link a raw span only after its existing trace is fully verified."""
    if spans:
        report = verify_trace_integrity(spans)
        if report.status is not TraceIntegrityStatus.VERIFIED:
            raise ValueError("cannot extend an unverified trace")
        previous_hash = spans[-1].integrity_hash
    else:
        previous_hash = None
    return seal_trace_span(
        span,
        sequence=len(spans) + 1,
        previous_hash=previous_hash,
    )


def trace_anchor(spans: Sequence[TraceSpan]) -> tuple[int, str | None]:
    """Return the verified prefix anchor used by one durable checkpoint."""
    report = verify_trace_integrity(spans)
    if report.status is not TraceIntegrityStatus.VERIFIED:
        raise ValueError("cannot anchor an unverified trace")
    return len(spans), spans[-1].integrity_hash if spans else None


def seal_checkpoint(checkpoint: Checkpoint) -> Checkpoint:
    """Return a checkpoint whose recovery fields are bound to its trace anchor."""
    if _checkpoint_integrity_mode(checkpoint) != "unsealed":
        raise ValueError("checkpoint already has integrity metadata")
    return checkpoint.model_copy(
        update={
            "checkpoint_integrity_version": _INTEGRITY_VERSION,
            "checkpoint_integrity_hash": _checkpoint_digest(checkpoint),
        }
    )


def verify_trace_integrity(
    spans: Sequence[TraceSpan],
    *,
    checkpoints: Sequence[Checkpoint] = (),
) -> TraceIntegrityReport:
    """Verify a hash-linked trace and optional checkpoint anchors without raw output."""
    if not spans:
        return TraceIntegrityReport(
            status=TraceIntegrityStatus.LEGACY_UNSEALED,
            span_count=0,
        )

    first = spans[0]
    modes = {_integrity_mode(item) for item in spans}
    if modes == {"unsealed"}:
        return TraceIntegrityReport(
            status=TraceIntegrityStatus.LEGACY_UNSEALED,
            trace_id=first.trace_id,
            session_id=first.session_id,
            span_count=len(spans),
        )

    mismatches: list[str] = []
    if modes != {"sealed"}:
        _add_once(mismatches, "trace_integrity_mixed_version")

    calculated_hashes: list[str] = []
    previous_hash: str | None = None
    for expected_sequence, item in enumerate(spans, start=1):
        if item.integrity_version != _INTEGRITY_VERSION:
            _add_once(mismatches, "trace_integrity_version_mismatch")
        if item.sequence != expected_sequence:
            _add_once(mismatches, "trace_integrity_sequence_mismatch")
        if item.previous_hash != previous_hash:
            _add_once(mismatches, "trace_integrity_previous_hash_mismatch")
        calculated_hash = _digest(
            item,
            sequence=expected_sequence,
            previous_hash=previous_hash,
        )
        if item.integrity_hash != calculated_hash:
            _add_once(mismatches, "trace_integrity_hash_mismatch")
        calculated_hashes.append(calculated_hash)
        previous_hash = calculated_hash

    anchored = _verify_checkpoint_anchors(
        checkpoints,
        session_id=first.session_id,
        calculated_hashes=calculated_hashes,
        mismatches=mismatches,
    )
    return TraceIntegrityReport(
        status=(
            TraceIntegrityStatus.VERIFIED
            if not mismatches
            else TraceIntegrityStatus.INVALID
        ),
        trace_id=first.trace_id,
        session_id=first.session_id,
        span_count=len(spans),
        anchored_checkpoint_count=anchored,
        mismatches=mismatches,
    )


def _verify_checkpoint_anchors(
    checkpoints: Sequence[Checkpoint],
    *,
    session_id: str,
    calculated_hashes: Sequence[str],
    mismatches: list[str],
) -> int:
    anchored = 0
    previous_count = 0
    for checkpoint in checkpoints:
        if checkpoint.session_id != session_id:
            _add_once(mismatches, "checkpoint_trace_session_mismatch")
            continue
        count = checkpoint.trace_span_count
        head_hash = checkpoint.trace_head_hash
        if count is None and head_hash is None:
            _add_once(mismatches, "checkpoint_trace_anchor_missing")
            continue
        if count is None or head_hash is None:
            _add_once(mismatches, "checkpoint_trace_anchor_incomplete")
            continue
        if count < 1 or count > len(calculated_hashes):
            _add_once(mismatches, "checkpoint_trace_span_count_mismatch")
            continue
        if count <= previous_count:
            _add_once(mismatches, "checkpoint_trace_order_mismatch")
        previous_count = count
        if head_hash != calculated_hashes[count - 1]:
            _add_once(mismatches, "checkpoint_trace_hash_mismatch")
            continue
        _verify_checkpoint_integrity(checkpoint, mismatches)
        anchored += 1
    return anchored


def _integrity_mode(span: TraceSpan) -> str:
    required = (span.integrity_version, span.sequence, span.integrity_hash)
    if all(value is None for value in required) and span.previous_hash is None:
        return "unsealed"
    if all(value is not None for value in required):
        return "sealed"
    return "partial"


def _checkpoint_integrity_mode(checkpoint: Checkpoint) -> str:
    version = checkpoint.checkpoint_integrity_version
    digest = checkpoint.checkpoint_integrity_hash
    if version is None and digest is None:
        return "unsealed"
    if version is not None and digest is not None:
        return "sealed"
    return "partial"


def _verify_checkpoint_integrity(checkpoint: Checkpoint, mismatches: list[str]) -> None:
    mode = _checkpoint_integrity_mode(checkpoint)
    if mode == "unsealed":
        _add_once(mismatches, "checkpoint_integrity_missing")
        return
    if mode == "partial":
        _add_once(mismatches, "checkpoint_integrity_incomplete")
        return
    if checkpoint.checkpoint_integrity_version != _INTEGRITY_VERSION:
        _add_once(mismatches, "checkpoint_integrity_version_mismatch")
    if checkpoint.checkpoint_integrity_hash != _checkpoint_digest(checkpoint):
        _add_once(mismatches, "checkpoint_integrity_hash_mismatch")


def _digest(span: TraceSpan, *, sequence: int, previous_hash: str | None) -> str:
    payload = {
        "integrity_version": _INTEGRITY_VERSION,
        "previous_hash": previous_hash,
        "sequence": sequence,
        "span": span.model_dump(mode="json", exclude=_INTEGRITY_FIELDS),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{_HASH_PREFIX}{sha256(canonical.encode('utf-8')).hexdigest()}"


def _checkpoint_digest(checkpoint: Checkpoint) -> str:
    payload = {
        "integrity_version": _INTEGRITY_VERSION,
        "checkpoint": checkpoint.model_dump(
            mode="json", exclude=_CHECKPOINT_INTEGRITY_FIELDS
        ),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{_HASH_PREFIX}{sha256(canonical.encode('utf-8')).hexdigest()}"


def _add_once(mismatches: list[str], code: str) -> None:
    if code not in mismatches:
        mismatches.append(code)
