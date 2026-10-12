import logging
from typing import Any

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.config import get_settings
from app.github.client import GitHubClient
from app.memory.ingestion import ingest_repository
from app.review.graph import review_graph
from app.review.state import PRReviewState, ReviewStatus

logger = logging.getLogger(__name__)


async def run_pr_review(
    ctx: dict[str, Any],
    workflow_id: str,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """Fetch PR changes and run the review workflow."""

    logger.info("Starting review: %s", workflow_id)

    try:
        repo = input_data["repo_full_name"]
        pr_number = int(input_data["pr_number"])

        async with GitHubClient() as github:
            diff = await github.get_pr_diff(repo, pr_number)
            files = await github.get_pr_files(repo, pr_number)

        if not diff.strip():
            raise ValueError("GitHub returned an empty PR diff.")

        changed_files = [
            file["filename"]
            for file in files
            if file.get("filename")
        ]

        initial_state: PRReviewState = {
            "workflow_id": workflow_id,
            "repo_full_name": repo,
            "pr_number": pr_number,
            "pr_title": input_data.get("pr_title", ""),
            "pr_body": input_data.get("pr_body", ""),
            "pr_diff": diff,
            "author_login": input_data.get("author_login", ""),
            "head_commit_sha": input_data.get("head_commit_sha", ""),
            "base_branch": input_data.get("base_branch", "main"),
            "changed_files": changed_files,
            "retrieved_context": "",
            "agent_results": [],
            "verdict": None,
            "final_findings": [],
            "review_posted": False,
            "github_review_id": None,
            "status": ReviewStatus.PENDING,
        }

        final_state = await review_graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": workflow_id}},
        )

        verdict = final_state.get("verdict")
        verdict = verdict.value if verdict else None

        logger.info(
            "Review completed: %s, verdict=%s",
            workflow_id,
            verdict,
        )

        return {
            "workflow_id": workflow_id,
            "status": "completed",
            "verdict": verdict,
            "findings_count": len(final_state.get("final_findings", [])),
        }

    except Exception:
        logger.exception("Review failed: %s", workflow_id)
        raise


async def ingest_repository_job(
    ctx: dict[str, Any],
    repo_full_name: str,
) -> dict[str, Any]:
    """Index a repository for RAG."""
    return await ingest_repository(repo_full_name)


async def enqueue_review_job(
    workflow_id: str,
    input_data: dict[str, Any],
) -> None:
    """Queue a PR review in Redis using ARQ."""

    settings = get_settings()
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    pool: ArqRedis = await create_pool(redis_settings)

    try:
        await pool.enqueue_job(
            "run_pr_review",
            workflow_id,
            input_data,
            _job_id=workflow_id,
        )
        logger.info("Review queued: %s", workflow_id)
    finally:
        await pool.aclose()


class WorkerSettings:
    """ARQ worker configuration."""

    functions = [run_pr_review, ingest_repository_job]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = get_settings().max_concurrent_reviews
    job_timeout = get_settings().workflow_timeout_seconds
    max_tries = 3