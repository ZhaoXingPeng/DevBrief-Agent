from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from devbrief.domain.contracts import ToolReceipt, ToolReceiptStatus, ToolRequest
from devbrief.domain.errors import DevBriefError, ErrorCode


class GitHubError(RuntimeError):
    """A redacted GitHub API failure."""


class GitHubUnknownOutcomeError(GitHubError):
    """The provider may have applied a write before the response was lost."""


@dataclass(frozen=True, slots=True)
class GitHubIssue:
    number: int | None
    url: str | None
    title: str
    dry_run: bool


class GitHubIssueClient:
    """Create and query GitHub Issues with an explicit dry-run default."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_url: str = "https://api.github.com",
        dry_run: bool = True,
        timeout: float = 15.0,
        allowed_repositories: set[str] | frozenset[str] | None = None,
    ) -> None:
        self.token = token or os.getenv("DEVBRIEF_GITHUB_TOKEN")
        self.api_url = api_url.rstrip("/")
        self.dry_run = dry_run
        self.timeout = timeout
        self.allowed_repositories = (
            frozenset(allowed_repositories)
            if allowed_repositories is not None
            else None
        )

    def create_issue(
        self,
        *,
        repository: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
        assignee: str | None = None,
        idempotency_key: str | None = None,
    ) -> GitHubIssue:
        _validate_repository(repository, self.allowed_repositories)
        if not title.strip() or not body.strip():
            raise GitHubError("title and body are required")
        if self.dry_run:
            return GitHubIssue(number=None, url=None, title=title, dry_run=True)
        if not self.token:
            raise GitHubError("DEVBRIEF_GITHUB_TOKEN is required when dry-run is off")

        payload_body = body
        if idempotency_key:
            marker = idempotency_marker(idempotency_key)
            if marker not in payload_body:
                payload_body = f"{payload_body.rstrip()}\n\n{marker}"
        payload: dict[str, object] = {"title": title, "body": payload_body}
        if labels:
            payload["labels"] = labels
        if assignee:
            payload["assignees"] = [assignee]
        request = Request(
            f"{self.api_url}/repos/{repository}/issues",
            data=json.dumps(payload).encode("utf-8"),
            headers=_headers(self.token),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                decoded: Any = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code >= 500:
                raise GitHubUnknownOutcomeError(
                    "GitHub Issue request outcome is unknown"
                ) from exc
            raise GitHubError("GitHub Issue request failed") from exc
        except (URLError, TimeoutError) as exc:
            raise GitHubUnknownOutcomeError(
                "GitHub Issue request outcome is unknown"
            ) from exc
        return _issue_from_response(decoded, title=title)

    def find_issue_by_idempotency(
        self, *, repository: str, idempotency_key: str
    ) -> GitHubIssue | None:
        """Find a previous write without issuing another external mutation."""
        _validate_repository(repository, self.allowed_repositories)
        if not idempotency_key:
            raise GitHubError("idempotency key is required")
        if self.dry_run:
            return None
        if not self.token:
            raise GitHubError("DEVBRIEF_GITHUB_TOKEN is required when dry-run is off")

        marker = idempotency_marker(idempotency_key)
        query = urlencode({"q": f'repo:{repository} "{marker}"', "per_page": "10"})
        request = Request(
            f"{self.api_url}/search/issues?{query}",
            headers=_headers(self.token),
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                decoded: Any = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise GitHubError("GitHub Issue query failed") from exc
        if not isinstance(decoded, dict):
            raise GitHubError("GitHub returned an invalid search response")
        result = cast(dict[str, Any], decoded)
        items = result.get("items")
        if not isinstance(items, list):
            raise GitHubError("GitHub search response omitted items")
        matches: list[dict[str, Any]] = []
        for item in cast(list[object], items):
            if not isinstance(item, dict):
                continue
            candidate = cast(dict[str, Any], item)
            if marker in str(candidate.get("body", "")):
                matches.append(candidate)
        if len(matches) > 1:
            raise GitHubError("GitHub returned multiple idempotency matches")
        if not matches:
            return None
        return _issue_from_response(matches[0], title="")


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
        repository, title, body = _required_text_arguments(request)
        labels_value = request.arguments.get("labels", [])
        assignee = request.arguments.get("assignee")
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
        try:
            issue = self.client.create_issue(
                repository=repository,
                title=title,
                body=body,
                labels=cast(list[str], labels_value),
                assignee=assignee,
                idempotency_key=request.idempotency_key,
            )
        except GitHubUnknownOutcomeError:
            self._counter += 1
            return ToolReceipt(
                receipt_id=f"rcpt_github_{self._counter}",
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                status=ToolReceiptStatus.UNKNOWN_OUTCOME,
                provider_request_id=f"github-unknown-{self._counter}",
                idempotency_key=request.idempotency_key,
                created_at=datetime.now(UTC),
            )
        except GitHubError as exc:
            raise _provider_error(exc) from exc
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
        """Query GitHub by the immutable marker bound to the write request."""
        if not request.idempotency_key:
            raise DevBriefError(
                ErrorCode.VALIDATION_ERROR, "idempotency key is required"
            )
        repository = request.arguments.get("repository")
        if not isinstance(repository, str):
            raise DevBriefError(ErrorCode.VALIDATION_ERROR, "repository is required")
        try:
            issue = self.client.find_issue_by_idempotency(
                repository=repository,
                idempotency_key=request.idempotency_key,
            )
        except GitHubError as exc:
            raise _provider_error(exc) from exc
        if issue is None:
            return None
        self._counter += 1
        return ToolReceipt(
            receipt_id=f"rcpt_github_query_{self._counter}",
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            status=ToolReceiptStatus.SUCCEEDED,
            external_object_id=(
                str(issue.number) if issue.number is not None else None
            ),
            external_url=issue.url,
            provider_request_id="github-query",
            idempotency_key=request.idempotency_key,
            created_at=datetime.now(UTC),
        )


def idempotency_marker(idempotency_key: str) -> str:
    """Return a non-secret marker suitable for an external Issue body/search."""
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"<!-- devbrief-idempotency:{digest} -->"


def _required_text_arguments(request: ToolRequest) -> tuple[str, str, str]:
    repository = request.arguments.get("repository")
    title = request.arguments.get("title")
    body = request.arguments.get("body")
    if not all(isinstance(item, str) for item in (repository, title, body)):
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR,
            "repository, title and body are required",
        )
    return cast(str, repository), cast(str, title), cast(str, body)


def _provider_error(error: GitHubError) -> DevBriefError:
    message = str(error)
    code = (
        ErrorCode.AUTH_ERROR
        if "TOKEN" in message
        else ErrorCode.TRANSIENT_PROVIDER_ERROR
    )
    return DevBriefError(code, message)


def _validate_repository(
    repository: str, allowed_repositories: frozenset[str] | None
) -> None:
    if not repository or "/" not in repository:
        raise GitHubError("repository must use owner/name format")
    owner, name = repository.split("/", maxsplit=1)
    if not owner or not name or "/" in name:
        raise GitHubError("repository must use owner/name format")
    if allowed_repositories is not None and repository not in allowed_repositories:
        raise GitHubError("repository is outside the configured write scope")


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _issue_from_response(value: object, *, title: str) -> GitHubIssue:
    if not isinstance(value, dict):
        raise GitHubError("GitHub returned an invalid response")
    result = cast(dict[str, Any], value)
    number = result.get("number")
    url = result.get("html_url")
    response_title = result.get("title")
    if not isinstance(number, int) or not isinstance(url, str):
        raise GitHubError("GitHub response omitted issue identity")
    return GitHubIssue(
        number=number,
        url=url,
        title=response_title if isinstance(response_title, str) else title,
        dry_run=False,
    )
