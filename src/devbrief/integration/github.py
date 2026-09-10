from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
