# app/workers/review_worker.py
"""
ARQ background worker and job enqueuer for PR reviews.
"""

from __future__ import annotations

import logging
from typing import Any

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.config import get_settings
from app.memory.ingestion import ingest_repository
from app.review.graph import review_graph
from app.review.state import PRReviewState, ReviewStatus

logger = logging.getLogger(__name__)


async def run_pr_review(
    ctx: dict[str, Any],
    workflow_id: str,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    """
    ARQ task: Executes the LangGraph PR review workflow.
    """
    logger.info("run_pr_review started | workflow_id=%s", workflow_id)

    initial_state: PRReviewState = {
        "workflow_id": workflow_id,
        "repo_full_name": input_data["repo_full_name"],
        "pr_number": int(input_data["pr_number"]),
        "pr_title": input_data.get("pr_title", ""),
        "pr_body": input_data.get("pr_body", ""),
        "pr_diff": input_data.get("pr_diff", ""),
        "author_login": input_data.get("author_login", ""),
        "head_commit_sha": input_data.get("head_commit_sha", ""),
        "base_branch": input_data.get("base_branch", "main"),
        "changed_files": input_data.get("changed_files", []),
        "retrieved_context": "",
        "confidence_threshold": get_settings().confidence_threshold,
        "agent_results": [],
        "verdict": None,
        "final_findings": [],
        "overall_confidence": 0.0,
        "needs_human_review": False,
        "human_review_reason": "",
        "review_posted": False,
        "github_review_id": None,
        "status": ReviewStatus.PENDING,
    }

    try:
        final_state = await review_graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": workflow_id}},
        )
        logger.info(
            "run_pr_review complete | workflow_id=%s verdict=%s",
            workflow_id,
            final_state.get("verdict"),
        )
        return {
            "workflow_id": workflow_id,
            "status": "completed",
            "verdict": str(final_state.get("verdict")),
            "findings_count": len(final_state.get("final_findings", [])),
        }
    except Exception as exc:
        logger.error("run_pr_review failed | workflow_id=%s error=%s", workflow_id, exc)
        return {
            "workflow_id": workflow_id,
            "status": "failed",
            "error": str(exc),
        }


async def ingest_repository_job(ctx: dict[str, Any], repo_full_name: str) -> dict[str, Any]:
    """ARQ task: Background repository ingestion."""
    return await ingest_repository(repo_full_name)


async def enqueue_review_job(
    workflow_id: str,
    input_data: dict[str, Any],
) -> None:
    """Enqueues a review job into the ARQ Redis queue."""
    cfg = get_settings()
    redis_settings = RedisSettings.from_dsn(cfg.redis_url)
    pool: ArqRedis = await create_pool(redis_settings)
    try:
        await pool.enqueue_job(
            "run_pr_review",
            workflow_id,
            input_data,
            _job_id=workflow_id,
        )
        logger.info("enqueue_review_job | workflow_id=%s queued", workflow_id)
    finally:
        await pool.aclose()


class WorkerSettings:
    """ARQ worker configuration."""

    functions = [run_pr_review, ingest_repository_job]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
    job_timeout = 300
    max_tries = 3
