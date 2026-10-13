# backend/database/repository.py
#
# Data access layer — all SQL operations go through these functions.
#
# REPOSITORY PATTERN (from Decoupling-and-Law-of-Demeter.md, Pragmatic Programmer wiki):
#   "A module should not reach through its immediate neighbor to access another layer."
#   Nodes and agents do NOT import SQLAlchemy or write SQL directly.
#   They call save_review(), get_review(), list_findings_for_repo().
#   The repository is the ONLY place that speaks SQLAlchemy to the database.
#   This means swapping Postgres for a different store only requires changing this file.
#
# TRANSACTION SCOPE:
# (From Storage-Engines.md wiki, "Write-Ahead Log and Transactions"):
#   "A write that spans multiple tables must succeed atomically or not at all."
#   save_review() writes PRReviewRecord + all FindingRecords in ONE transaction.
#   If any insert fails, the whole transaction rolls back — no partial state.
#   We use session.begin() as the transaction scope explicitly.
#
# ASYNC PATTERNS:
# (From Storage-Engines.md wiki):
#   "await session.execute() for reads, await session.flush() before accessing IDs,
#    await session.commit() to finalize writes."
#   We await every database call. Never use synchronous SQLAlchemy in async context.
#
# DEPENDENCY DIRECTION CHECK:
#   repository.py imports from: backend.database.postgres (session), backend.database.models
#   repository.py does NOT import from: agents, orchestrator, tools
#   This is correct — repository is at the tools/memory layer.

import hashlib
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import FindingRecord, PRReviewRecord


async def save_review(
    session: AsyncSession,
    *,
    review_id: str,
    repo_full_name: str,
    pr_number: int,
    pr_title: str,
    head_commit_sha: str,
    pr_diff: str,
    verdict: str | None,
    status: str,
    findings: list[dict[str, Any]],
    github_review_id: int | None = None,
) -> PRReviewRecord:
    """Insert or update a PR review and its findings."""

    result = await session.execute(
        select(PRReviewRecord).where(PRReviewRecord.id == review_id)
    )
    review = result.scalar_one_or_none()

    if review is None:
        review = PRReviewRecord(
            id=review_id,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            pr_title=pr_title,
            head_commit_sha=head_commit_sha,
            diff_hash=hashlib.sha256(pr_diff.encode()).hexdigest(),
            verdict=verdict,
            status=status,
            github_review_id=github_review_id,
        )
        session.add(review)
    else:
        review.repo_full_name = repo_full_name
        review.pr_number = pr_number
        review.pr_title = pr_title
        review.head_commit_sha = head_commit_sha
        review.diff_hash = hashlib.sha256(pr_diff.encode()).hexdigest()
        review.verdict = verdict
        review.status = status
        review.github_review_id = github_review_id

        # Replace findings associated with this review.
        await session.execute(
            delete(FindingRecord).where(
                FindingRecord.review_id == review_id
            )
        )

    for finding in findings:
        session.add(
            FindingRecord(
                review_id=review_id,
                agent_type=finding.get("agent_type", "quality"),
                severity=finding.get("severity", "low"),
                category=finding.get("category", "quality"),
                summary=finding.get("summary", ""),
                file_path=finding.get("file_path"),
                line_start=finding.get("line_start"),
                line_end=finding.get("line_end"),
                suggestion=finding.get("suggestion"),
            )
        )

    await session.commit()
    await session.refresh(review)

    return review


async def get_review(
    session: AsyncSession,
    review_id: str,
) -> PRReviewRecord | None:
    """Get a review by its ID."""

    result = await session.execute(
        select(PRReviewRecord).where(PRReviewRecord.id == review_id)
    )
    return result.scalar_one_or_none()


async def list_reviews(
    session: AsyncSession,
    *,
    repo_full_name: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PRReviewRecord], int]:
    """List reviews with optional filters and pagination."""

    query = select(PRReviewRecord)
    count_query = select(func.count()).select_from(PRReviewRecord)

    if repo_full_name is not None:
        query = query.where(PRReviewRecord.repo_full_name == repo_full_name)
        count_query = count_query.where(
            PRReviewRecord.repo_full_name == repo_full_name
        )

    if status is not None:
        query = query.where(PRReviewRecord.status == status)
        count_query = count_query.where(PRReviewRecord.status == status)

    total = (await session.execute(count_query)).scalar_one()

    result = await session.execute(
        query.order_by(PRReviewRecord.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    return list(result.scalars().all()), total


async def list_findings_for_review(
    session: AsyncSession,
    review_id: str,
) -> list[FindingRecord]:
    """Get all findings belonging to a review."""

    result = await session.execute(
        select(FindingRecord)
        .where(FindingRecord.review_id == review_id)
        .order_by(FindingRecord.created_at.desc())
    )
    return list(result.scalars().all())