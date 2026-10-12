import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class GitHubClient:
    """Small async client for the GitHub REST API."""

    def __init__(self, settings=None):
        settings = settings or get_settings()

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        self.base_url = settings.github_api_base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
        )

    async def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Fetch the PR diff."""
        response = await self.client.get(
            f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        response.raise_for_status()
        return response.text

    async def get_pr_files(
        self,
        repo_full_name: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        """Fetch files changed in the PR."""
        files = []
        page = 1

        while True:
            response = await self.client.get(
                f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}/files",
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()

            page_files = response.json()
            files.extend(page_files)

            if len(page_files) < 100:
                break

            page += 1

        return files

    async def post_pr_review(
        self,
        repo_full_name: str,
        pr_number: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Post a review to GitHub."""
        response = await self.client.post(
            f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}/reviews",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()