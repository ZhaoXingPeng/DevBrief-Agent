from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from devbrief.domain.contracts import Plan


class TaskSystemError(RuntimeError):
    """A redacted task-system draft failure."""


@dataclass(frozen=True, slots=True)
class TaskDraftRecord:
    draft_id: str
    url: str | None
    dry_run: bool


class TaskDraftBackend(Protocol):
    def create_draft(self, plan: Plan) -> TaskDraftRecord: ...


class TaskSystemDraftClient:
    """Configurable JSON draft endpoint; it never creates an external issue."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        token: str | None = None,
        dry_run: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.endpoint = endpoint or os.getenv("DEVBRIEF_TASK_DRAFT_URL")
        self.token = token or os.getenv("DEVBRIEF_TASK_DRAFT_TOKEN")
        self.dry_run = dry_run
        self.timeout = timeout

    def create_draft(self, plan: Plan) -> TaskDraftRecord:
        if self.dry_run:
            return TaskDraftRecord(
                draft_id=f"draft-{_plan_digest(plan)[:16]}", url=None, dry_run=True
            )
        if not self.endpoint:
            raise TaskSystemError(
                "DEVBRIEF_TASK_DRAFT_URL is required when dry-run is off"
            )
        payload = json.dumps(plan.model_dump(mode="json"), ensure_ascii=True).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            with urlopen(
                Request(self.endpoint, data=payload, headers=headers, method="POST"),
                timeout=self.timeout,
            ) as response:
                value: Any = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TaskSystemError("task system draft request failed") from exc
        if not isinstance(value, dict):
            raise TaskSystemError("task system draft response is invalid")
        data = cast(dict[str, Any], value)
        raw_id = data.get("id")
        if not isinstance(raw_id, str):
            raise TaskSystemError("task system draft response is invalid")
        url_value = data.get("url")
        return TaskDraftRecord(
            draft_id=raw_id,
            url=url_value if isinstance(url_value, str) else None,
            dry_run=False,
        )


def _plan_digest(plan: Plan) -> str:
    import hashlib

    canonical = json.dumps(
        plan.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
