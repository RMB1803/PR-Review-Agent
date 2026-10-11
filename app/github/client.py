# app/github/client.py
"""
GitHub REST API client and data models.
Retrieves PR metadata, unified diffs, changed files, and posts review comments.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
import logging
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from app.config import Settings

logger = logging.getLogger(__name__)


# =============================================================================
# MODELS
# =============================================================================

class PRMetadata(BaseModel):
    number: int
    title: str
    body: str = ""
    author_login: str = Field(alias="user_login")
    head_sha: str = Field(alias="head_commit_sha")
    base_branch: str = Field(alias="base_branch_name")
    changed_files_count: int = 0

    model_config = {"populate_by_name": True}

    @classmethod
    def from_github_response(cls, data: dict[str, Any]) -> PRMetadata:
        return cls(
            number=data["number"],
            title=data.get("title", ""),
            body=data.get("body") or "",
            user_login=data.get("user", {}).get("login", "unknown"),
            head_commit_sha=data.get("head", {}).get("sha", ""),
            base_branch_name=data.get("base", {}).get("ref", "main"),
            changed_files_count=data.get("changed_files", 0),
        )


class PRFileStatus(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"
    COPIED = "copied"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


class PRFile(BaseModel):
    filename: str
    status: PRFileStatus = PRFileStatus.MODIFIED
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    patch: Optional[str] = None

    @classmethod
    def from_github_response(cls, data: dict[str, Any]) -> PRFile:
        return cls(
            filename=data["filename"],
            status=PRFileStatus(data.get("status", "modified")),
            additions=data.get("additions", 0),
            deletions=data.get("deletions", 0),
            changes=data.get("changes", 0),
            patch=data.get("patch"),
        )


class ReviewEvent(str, Enum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


class ReviewComment(BaseModel):
    path: str
    line: int
    body: str
    side: str = "RIGHT"


class PostReviewPayload(BaseModel):
    commit_id: str
    body: str
    event: ReviewEvent
    comments: list[ReviewComment] = Field(default_factory=list)


class PostReviewResponse(BaseModel):
    id: int
    state: ReviewEvent
    submitted_at: Optional[datetime] = None
    html_url: Optional[str] = None

    @classmethod
    def from_github_response(cls, data: dict[str, Any]) -> PostReviewResponse:
        state_map = {
            "APPROVED": ReviewEvent.APPROVE,
            "CHANGES_REQUESTED": ReviewEvent.REQUEST_CHANGES,
            "COMMENTED": ReviewEvent.COMMENT,
            "DISMISSED": ReviewEvent.COMMENT,
            "PENDING": ReviewEvent.COMMENT,
            "APPROVE": ReviewEvent.APPROVE,
            "REQUEST_CHANGES": ReviewEvent.REQUEST_CHANGES,
            "COMMENT": ReviewEvent.COMMENT,
        }
        raw_state = data.get("state", "COMMENTED")
        mapped = state_map.get(raw_state, ReviewEvent.COMMENT)
        return cls(
            id=data["id"],
            state=mapped,
            submitted_at=data.get("submitted_at"),
            html_url=data.get("html_url"),
        )


# =============================================================================
# EXCEPTIONS
# =============================================================================

class GitHubAPIError(Exception):
    def __init__(self, message: str, status_code: int = 0, response_body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body[:500] if response_body else ""

    def __str__(self) -> str:
        base = super().__str__()
        if self.status_code:
            return f"{base} (HTTP {self.status_code})"
        return base


class GitHubNotFoundError(GitHubAPIError):
    pass


class GitHubRateLimitError(GitHubAPIError):
    def __init__(
        self,
        message: str,
        status_code: int = 429,
        response_body: str = "",
        retry_after_seconds: int = 60,
    ) -> None:
        super().__init__(message, status_code, response_body)
        self.retry_after_seconds = retry_after_seconds


# =============================================================================
# CLIENT
# =============================================================================

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_RETRIES = 3
_BACKOFF_SECONDS = [1.0, 2.0, 4.0]
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
_MAX_DIFF_BYTES = 500_000


class GitHubClient:
    """Async client for the GitHub REST API."""

    def __init__(self, settings: Settings) -> None:
        self._token = settings.github_token
        self._base_url = settings.github_api_base_url.rstrip("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pr-review-agent/1.0",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=_TIMEOUT,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def get_pr_metadata(self, repo_full_name: str, pr_number: int) -> PRMetadata:
        path = f"/repos/{repo_full_name}/pulls/{pr_number}"
        data = await self._request("GET", path)
        return PRMetadata.from_github_response(data)

    async def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        path = f"/repos/{repo_full_name}/pulls/{pr_number}"
        raw_diff_bytes = await self._request_raw(
            "GET",
            path,
            headers={"Accept": "application/vnd.github.diff"},
        )

        if len(raw_diff_bytes) > _MAX_DIFF_BYTES:
            original_size = len(raw_diff_bytes)
            raw_diff_bytes = raw_diff_bytes[:_MAX_DIFF_BYTES]
            truncation_notice = (
                f"\n\n# [PR-REVIEW: DIFF TRUNCATED - original {original_size} bytes]\n"
            )
            raw_diff_bytes += truncation_notice.encode()

        return raw_diff_bytes.decode("utf-8", errors="replace")

    async def get_pr_files(self, repo_full_name: str, pr_number: int) -> list[PRFile]:
        all_files: list[PRFile] = []
        page = 1
        per_page = 100

        while True:
            path = f"/repos/{repo_full_name}/pulls/{pr_number}/files?per_page={per_page}&page={page}"
            page_data = await self._request("GET", path)

            if not isinstance(page_data, list):
                break

            for file_data in page_data:
                all_files.append(PRFile.from_github_response(file_data))

            if len(page_data) < per_page:
                break
            page += 1

        return all_files

    async def post_pr_review(
        self,
        repo_full_name: str,
        pr_number: int,
        payload: PostReviewPayload,
    ) -> PostReviewResponse:
        path = f"/repos/{repo_full_name}/pulls/{pr_number}/reviews"
        payload_dict = payload.model_dump(mode="json")
        data = await self._request("POST", path, json=payload_dict)
        return PostReviewResponse.from_github_response(data)

    async def _request(
        self,
        method: str,
        path: str,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._http.request(
                    method,
                    path,
                    json=json,
                    headers=headers or {},
                )
            except httpx.TimeoutException as e:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise GitHubAPIError(f"GitHub API timed out: {path}") from e
            except httpx.NetworkError as e:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise GitHubAPIError(f"GitHub API network error: {path}") from e

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                raise GitHubRateLimitError(
                    "GitHub rate limit exceeded",
                    status_code=429,
                    response_body=response.text,
                    retry_after_seconds=retry_after,
                )

            if response.status_code == 404:
                raise GitHubNotFoundError(
                    f"Resource not found: {path}",
                    status_code=404,
                    response_body=response.text,
                )

            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise GitHubAPIError(
                    f"GitHub API returned {response.status_code}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            if not response.is_success:
                raise GitHubAPIError(
                    f"GitHub API error {response.status_code}: {path}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            return response.json()

        raise GitHubAPIError(f"GitHub API request failed: {path}")

    async def _request_raw(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        for attempt in range(_MAX_RETRIES):
            try:
                merged_headers = dict(self._http.headers)
                if headers:
                    merged_headers.update(headers)
                response = await self._http.request(
                    method,
                    path,
                    headers=merged_headers,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise GitHubAPIError(f"GitHub diff request failed: {path}") from e

            if response.status_code == 404:
                raise GitHubNotFoundError(f"Resource not found: {path}", status_code=404)
            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise GitHubAPIError(f"GitHub diff returned {response.status_code}")
            if not response.is_success:
                raise GitHubAPIError(f"GitHub diff error {response.status_code}: {path}")

            return response.content

        raise GitHubAPIError(f"GitHub raw request failed: {path}")
