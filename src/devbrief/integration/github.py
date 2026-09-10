from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from devbrief.domain.contracts import ToolReceipt, ToolReceiptStatus, ToolRequest
from devbrief.domain.errors import DevBriefError, ErrorCode


class GitHubError(RuntimeError):
    """A redacted GitHub API failure."""


@dataclass(frozen=True, slots=True)
class GitHubIssue:
    number: int | None
    url: str | None
    title: str
    dry_run: bool


class GitHubIssueClient:
    """Create GitHub Issues with an explicit dry-run default."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_url: str = "https://api.github.com",
        dry_run: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.token = token or os.getenv("DEVBRIEF_GITHUB_TOKEN")
        self.api_url = api_url.rstrip("/")
        self.dry_run = dry_run
        self.timeout = timeout

    def create_issue(
        self,
        *,
        repository: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
        assignee: str | None = None,
    ) -> GitHubIssue:
        if not repository or "/" not in repository:
            raise GitHubError("repository must use owner/name format")
        if not title.strip() or not body.strip():
            raise GitHubError("title and body are required")
        if self.dry_run:
            return GitHubIssue(number=None, url=None, title=title, dry_run=True)
        if not self.token:
            raise GitHubError("DEVBRIEF_GITHUB_TOKEN is required when dry-run is off")

        payload: dict[str, object] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if assignee:
            payload["assignees"] = [assignee]
        request = Request(
            f"{self.api_url}/repos/{repository}/issues",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                decoded: Any = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise GitHubError("GitHub Issue request failed") from exc
        if not isinstance(decoded, dict):
            raise GitHubError("GitHub returned an invalid response")
        result = cast(dict[str, Any], decoded)
        number = result.get("number")
        url = result.get("html_url")
        if not isinstance(number, int) or not isinstance(url, str):
            raise GitHubError("GitHub response omitted issue identity")
        return GitHubIssue(number=number, url=url, title=title, dry_run=False)


class GitHubIssueProvider:
    """Receipt-producing adapter used by the approval-controlled writer."""

    def __init__(self, client: GitHubIssueClient) -> None:
        self.client = client
        self._counter = 0

    def create(self, request: ToolRequest) -> ToolReceipt:
        if not request.idempotency_key:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "idempotency key is required"
            )
        args = request.arguments
        repository = args.get("repository")
        title = args.get("title")
        body = args.get("body")
        if not all(isinstance(item, str) for item in (repository, title, body)):
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR,
                "repository, title and body are required",
            )
        repository_value = cast(str, repository)
        title_value = cast(str, title)
        body_value = cast(str, body)
        labels_value = args.get("labels", [])
        assignee = args.get("assignee")
        labels_items = (
            cast(list[object], labels_value) if isinstance(labels_value, list) else []
        )
        if not isinstance(labels_value, list) or not all(
            isinstance(item, str) for item in labels_items
        ):
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "labels must be a list of strings"
            )
        if assignee is not None and not isinstance(assignee, str):
            raise DevBriefError(ErrorCode.VALIDATION_ERROR, "assignee must be a string")
        labels = cast(list[str], labels_value)
        try:
            issue = self.client.create_issue(
                repository=repository_value,
                title=title_value,
                body=body_value,
                labels=labels,
                assignee=assignee,
            )
        except GitHubError as exc:
            message = str(exc)
            code = (
                ErrorCode.AUTH_ERROR
                if "TOKEN" in message
                else ErrorCode.TRANSIENT_PROVIDER_ERROR
            )
            raise DevBriefError(code, message) from exc
        self._counter += 1
        return ToolReceipt(
            receipt_id=f"rcpt_github_{self._counter}",
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            status=ToolReceiptStatus.SUCCEEDED,
            external_object_id=(
                str(issue.number) if issue.number is not None else None
            ),
            external_url=issue.url,
            provider_request_id=(
                "dry-run" if issue.dry_run else f"github-{self._counter}"
            ),
            idempotency_key=request.idempotency_key,
            created_at=datetime.now(UTC),
        )

    def query(self, request: ToolRequest) -> ToolReceipt | None:
        """GitHub has no safe generic idempotency query in this adapter."""
        del request
        return None
