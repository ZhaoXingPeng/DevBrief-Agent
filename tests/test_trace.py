from __future__ import annotations

from devbrief.domain.trace import redact_summary


def test_redact_summary_removes_credential_assignments_and_caps_length() -> None:
    summary = redact_summary(
        "token=private-value authorization: Bearer top-secret "
        "github_pat_private_value_12345",
        limit=200,
    )

    assert summary == ("token=[REDACTED] authorization: Bearer [REDACTED] [REDACTED]")
    assert len(redact_summary("x" * 100, limit=20)) == 20
