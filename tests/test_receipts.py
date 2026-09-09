from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from devbrief.application.receipts import (
    FakeReceiptQueryAdapter,
    InMemoryReceiptRepository,
    ReceiptRecoveryService,
)
from devbrief.domain.contracts import ToolReceipt, ToolReceiptStatus, ToolRequest
from devbrief.domain.errors import DevBriefError, ErrorCode

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def request(
    *,
    tool_name: str = "create_issue",
    arguments: dict[str, object] | None = None,
    tool_call_id: str = "call_1",
    idempotency_key: str = "idem_1",
) -> ToolRequest:
    return ToolRequest(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments or {"title": "Fix auth refresh"},
        session_id="ses_receipt",
        trace_id="trc_ses_receipt",
        idempotency_key=idempotency_key,
    )


def receipt(
    *,
    status: ToolReceiptStatus = ToolReceiptStatus.SUCCEEDED,
    tool_name: str = "create_issue",
    idempotency_key: str = "idem_1",
) -> ToolReceipt:
    return ToolReceipt(
        receipt_id="rcpt_1",
        tool_call_id="call_1",
        tool_name=tool_name,
        status=status,
        external_object_id="issue-42",
        external_url="https://fake.invalid/issues/42",
        provider_request_id="provider-7",
        idempotency_key=idempotency_key,
        created_at=NOW,
    )


def test_repository_reuses_same_idempotency_key_without_overwriting_receipt() -> None:
    repository = InMemoryReceiptRepository()
    first_request = request()
    first = receipt()
    repository.save(first_request, first)

    repeated = repository.find(request(tool_call_id="call_2"))

    assert repeated == first
    assert repository.get("rcpt_1") == first


def test_repository_rejects_conflicting_tool_or_arguments_for_same_key() -> None:
    repository = InMemoryReceiptRepository()
    repository.save(request(), receipt())

    for conflicting in (
        request(tool_name="other_write"),
        request(arguments={"title": "Different"}),
    ):
        with pytest.raises(DevBriefError) as raised:
            repository.find(conflicting)
        assert raised.value.code is ErrorCode.CONFLICT


def test_unknown_outcome_recovery_queries_first_and_upgrades_when_confirmed() -> None:
    repository = InMemoryReceiptRepository()
    original_request = request()
    unknown = receipt(status=ToolReceiptStatus.UNKNOWN_OUTCOME)
    repository.save(original_request, unknown)
    query = FakeReceiptQueryAdapter()
    confirmed = receipt(status=ToolReceiptStatus.SUCCEEDED)
    query.set_result(original_request, confirmed)
    service = ReceiptRecoveryService(repository, query)

    recovered = service.recover(original_request)

    assert recovered == confirmed
    assert query.calls == [original_request]
    assert repository.find(original_request) == confirmed
    assert service.traces[-1].output_summary == "query=found; status=succeeded"


def test_unknown_outcome_without_confirmation_remains_unknown_and_never_retries() -> (
    None
):
    repository = InMemoryReceiptRepository()
    original_request = request()
    unknown = receipt(status=ToolReceiptStatus.UNKNOWN_OUTCOME)
    repository.save(original_request, unknown)
    query = FakeReceiptQueryAdapter()
    service = ReceiptRecoveryService(repository, query)

    recovered = service.recover(original_request)

    assert recovered == unknown
    assert query.calls == [original_request]
    assert (
        service.traces[-1].output_summary == "query=not_found; status=unknown_outcome"
    )


def test_receipt_validation_and_trace_are_metadata_only() -> None:
    repository = InMemoryReceiptRepository()
    invalid = receipt().model_copy(update={"idempotency_key": ""})
    with pytest.raises(DevBriefError) as raised:
        repository.save(request(), invalid)
    assert raised.value.code is ErrorCode.VALIDATION_ERROR

    private = receipt().model_copy(
        update={"provider_request_id": "token=private-value"}
    )
    repository.save(request(), private)
    saved = repository.get("rcpt_1")
    assert saved is not None
    serialized = json.dumps(saved.model_dump(mode="json"))
    assert "private-value" not in serialized
    assert "token=[REDACTED]" in serialized
