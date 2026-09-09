from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

from pydantic import ValidationError

from devbrief.domain.contracts import (
    ToolReceipt,
    ToolReceiptStatus,
    ToolRequest,
    TraceKind,
    TraceSpan,
)
from devbrief.domain.errors import DevBriefError, ErrorCode
from devbrief.domain.trace import redact_summary


class InMemoryReceiptRepository:
    """Store fake receipts with a conflict-checked, process-local idempotency index."""

    def __init__(self) -> None:
        self._by_receipt_id: dict[str, ToolReceipt] = {}
        self._by_idempotency: dict[str, tuple[ToolRequest, ToolReceipt]] = {}

    def save(self, request: ToolRequest, receipt: ToolReceipt) -> ToolReceipt:
        """Persist one receipt or return the existing receipt for the same request."""
        normalized_request = _validate_request(request)
        normalized_receipt = _validate_receipt(receipt)
        if normalized_receipt.tool_name != normalized_request.tool_name:
            raise DevBriefError(
                ErrorCode.CONFLICT, "receipt tool does not match request"
            )
        if normalized_receipt.idempotency_key != normalized_request.idempotency_key:
            raise DevBriefError(
                ErrorCode.CONFLICT, "receipt idempotency key does not match request"
            )
        existing_item = self._by_idempotency.get(
            normalized_request.idempotency_key or ""
        )
        if existing_item is not None:
            existing_request, existing_receipt = existing_item
            if not _same_operation(existing_request, normalized_request):
                raise DevBriefError(
                    ErrorCode.CONFLICT,
                    "idempotency key is already bound to another operation",
                )
            if (
                existing_receipt.status is ToolReceiptStatus.UNKNOWN_OUTCOME
                and normalized_receipt.status is ToolReceiptStatus.SUCCEEDED
            ):
                self._by_receipt_id[existing_receipt.receipt_id] = normalized_receipt
                self._by_idempotency[normalized_request.idempotency_key or ""] = (
                    existing_request,
                    normalized_receipt,
                )
                return normalized_receipt
            return existing_receipt
        if normalized_receipt.receipt_id in self._by_receipt_id:
            raise DevBriefError(ErrorCode.CONFLICT, "receipt id already exists")
        self._by_receipt_id[normalized_receipt.receipt_id] = normalized_receipt
        self._by_idempotency[normalized_request.idempotency_key or ""] = (
            normalized_request,
            normalized_receipt,
        )
        return normalized_receipt

    def get(self, receipt_id: str) -> ToolReceipt | None:
        return self._by_receipt_id.get(receipt_id)

    def find(self, request: ToolRequest) -> ToolReceipt | None:
        normalized_request = _validate_request(request)
        existing_item = self._by_idempotency.get(
            normalized_request.idempotency_key or ""
        )
        if existing_item is None:
            return None
        existing_request, existing_receipt = existing_item
        if not _same_operation(existing_request, normalized_request):
            raise DevBriefError(
                ErrorCode.CONFLICT,
                "idempotency key is already bound to another operation",
            )
        return existing_receipt


class ReceiptQueryAdapter(Protocol):
    def query(self, request: ToolRequest) -> ToolReceipt | None: ...


class FakeReceiptQueryAdapter:
    """Deterministic query-only fake; it never creates or retries an external object."""

    def __init__(self) -> None:
        self._results: dict[str, ToolReceipt] = {}
        self.calls: list[ToolRequest] = []

    def set_result(self, request: ToolRequest, receipt: ToolReceipt) -> None:
        _validate_request(request)
        self._results[_operation_key(request)] = _validate_receipt(receipt)

    def query(self, request: ToolRequest) -> ToolReceipt | None:
        normalized_request = _validate_request(request)
        self.calls.append(normalized_request)
        return self._results.get(_operation_key(normalized_request))


class ReceiptRecoveryService:
    """Resolve unknown writes by querying first and never issuing a blind retry."""

    def __init__(
        self,
        repository: InMemoryReceiptRepository,
        query_adapter: ReceiptQueryAdapter,
    ) -> None:
        self.repository = repository
        self.query_adapter = query_adapter
        self.traces: list[TraceSpan] = []

    def recover(self, request: ToolRequest) -> ToolReceipt:
        """Return a known receipt, or preserve unknown status after a query miss."""
        existing = self.repository.find(request)
        if existing is None:
            raise DevBriefError(
                ErrorCode.CHECKPOINT_UNAVAILABLE,
                "no receipt exists for recovery",
            )
        if existing.status is ToolReceiptStatus.SUCCEEDED:
            self._trace(request, "query=skipped; status=succeeded")
            return existing
        if existing.status is not ToolReceiptStatus.UNKNOWN_OUTCOME:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "only unknown outcomes can be recovered",
            )
        confirmed = self.query_adapter.query(request)
        if confirmed is None:
            self._trace(request, "query=not_found; status=unknown_outcome")
            return existing
        recovered = confirmed.model_copy(
            update={
                "tool_call_id": existing.tool_call_id,
                "tool_name": existing.tool_name,
                "idempotency_key": existing.idempotency_key,
                "status": ToolReceiptStatus.SUCCEEDED,
            }
        )
        saved = self.repository.save(request, recovered)
        self._trace(request, "query=found; status=succeeded")
        return saved

    def _trace(self, request: ToolRequest, output: str) -> None:
        self.traces.append(
            TraceSpan(
                span_id=f"receipt_recovery_{len(self.traces) + 1}",
                trace_id=request.trace_id or "trc_unbound",
                session_id=request.session_id,
                kind=TraceKind.TOOL_CALL,
                input_summary=(
                    f"tool={redact_summary(request.tool_name)}; query_only=true"
                ),
                output_summary=redact_summary(output),
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                tool_decision="query_only",
                receipt_id=self.repository.find(request).receipt_id,  # type: ignore[union-attr]
            )
        )


def _validate_request(request: ToolRequest) -> ToolRequest:
    if not request.idempotency_key:
        raise DevBriefError(ErrorCode.VALIDATION_ERROR, "idempotency key is required")
    return request


def _validate_receipt(receipt: ToolReceipt) -> ToolReceipt:
    try:
        validated = ToolReceipt.model_validate(receipt.model_dump(mode="json"))
    except ValidationError as exc:
        raise DevBriefError(ErrorCode.VALIDATION_ERROR, "invalid tool receipt") from exc
    sanitized = validated.model_copy(
        update={
            "external_object_id": _redact_optional(validated.external_object_id),
            "external_url": _redact_optional(validated.external_url),
            "provider_request_id": _redact_optional(validated.provider_request_id),
        }
    )
    return sanitized


def _redact_optional(value: str | None) -> str | None:
    return redact_summary(value) if value is not None else None


def _same_operation(left: ToolRequest, right: ToolRequest) -> bool:
    return left.tool_name == right.tool_name and _canonical(
        left.arguments
    ) == _canonical(right.arguments)


def _operation_key(request: ToolRequest) -> str:
    return request.idempotency_key or ""


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
