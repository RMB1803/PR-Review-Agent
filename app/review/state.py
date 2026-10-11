# app/review/state.py
"""
Workflow state definitions for the PR review LangGraph.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing_extensions import TypedDict


class ReviewStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AgentResultState(TypedDict):
    agent_type: str
    success: bool
    error_message: str
    duration_seconds: float
    findings: list[dict[str, Any]]
    confidence: float
    per_verdict: str


class PRReviewState(TypedDict):
    workflow_id: str
    repo_full_name: str
    pr_number: int
    pr_title: str
    pr_body: str
    pr_diff: str
    author_login: str
    head_commit_sha: str
    base_branch: str
    changed_files: list[str]
    retrieved_context: str
    confidence_threshold: float
    agent_results: list[AgentResultState]
    verdict: ReviewVerdict | None
    final_findings: list[dict[str, Any]]
    overall_confidence: float
    needs_human_review: bool
    human_review_reason: str
    review_posted: bool
    github_review_id: int | None
    status: ReviewStatus
