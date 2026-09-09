from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from devbrief.domain.contracts import TranscriptFixture
from devbrief.domain.errors import DevBriefError, ErrorCode


def load_fixture(path: Path) -> TranscriptFixture:
    """Load one versioned, redacted transcript fixture from local JSON."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TranscriptFixture.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "invalid transcript fixture"
        ) from exc
