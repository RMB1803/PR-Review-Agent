# tests/test_review.py
"""
Tests for LangGraph review workflow and aggregation logic.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.review.aggregator import aggregate_agent_findings, sort_findings
from app.review.graph import review_graph
from app.review.state import PRReviewState, ReviewStatus, ReviewVerdict


def test_sort_findings():
    findings = [
        {"severity": "low", "summary": "minor styling"},
        {"severity": "critical", "summary": "sql injection"},
        {"severity": "medium", "summary": "too complex"},
        {"severity": "high", "summary": "missing auth"},
    ]
    sorted_f = sort_findings(findings)
    assert [f["severity"] for f in sorted_f] == ["critical", "high", "medium", "low"]


def test_aggregate_agent_findings_security_failure():
    state: PRReviewState = {
        "workflow_id": "test:1:sha",
        "repo_full_name": "test/repo",
        "pr_number": 1,
        "pr_title": "Fix",
        "pr_body": "",
        "pr_diff": "",
        "author_login": "dev",
        "head_commit_sha": "sha",
        "base_branch": "main",
        "changed_files": [],
        "retrieved_context": "",
        "confidence_threshold": 0.7,
        "agent_results": [
            {
                "agent_type": "security",
                "success": False,
                "error_message": "timeout",
                "duration_seconds": 1.0,
                "findings": [],
                "confidence": 0.0,
                "per_verdict": "approve",
            },
            {
                "agent_type": "quality",
                "success": True,
                "error_message": "",
                "duration_seconds": 1.0,
                "findings": [],
                "confidence": 0.9,
                "per_verdict": "approve",
            },
        ],
        "verdict": None,
        "final_findings": [],
        "overall_confidence": 0.0,
        "needs_human_review": False,
        "human_review_reason": "",
        "review_posted": False,
        "github_review_id": None,
        "status": ReviewStatus.IN_PROGRESS,
    }

    res = aggregate_agent_findings(state)
    assert res["verdict"] == ReviewVerdict.NEEDS_HUMAN_REVIEW
    assert res["needs_human_review"] is True


def test_aggregate_agent_findings_approve():
    state: PRReviewState = {
        "workflow_id": "test:1:sha",
        "repo_full_name": "test/repo",
        "pr_number": 1,
        "pr_title": "Fix",
        "pr_body": "",
        "pr_diff": "",
        "author_login": "dev",
        "head_commit_sha": "sha",
        "base_branch": "main",
        "changed_files": [],
        "retrieved_context": "",
        "confidence_threshold": 0.7,
        "agent_results": [
            {
                "agent_type": "security",
                "success": True,
                "error_message": "",
                "duration_seconds": 1.0,
                "findings": [],
                "confidence": 0.95,
                "per_verdict": "approve",
            },
            {
                "agent_type": "quality",
                "success": True,
                "error_message": "",
                "duration_seconds": 1.0,
                "findings": [],
                "confidence": 0.9,
                "per_verdict": "approve",
            },
        ],
        "verdict": None,
        "final_findings": [],
        "overall_confidence": 0.0,
        "needs_human_review": False,
        "human_review_reason": "",
        "review_posted": False,
        "github_review_id": None,
        "status": ReviewStatus.IN_PROGRESS,
    }

    res = aggregate_agent_findings(state)
    assert res["verdict"] == ReviewVerdict.APPROVE
    assert res["needs_human_review"] is False


@pytest.mark.asyncio
async def test_review_graph_execution():
    initial_state: PRReviewState = {
        "workflow_id": "org/repo:1:sha123",
        "repo_full_name": "org/repo",
        "pr_number": 1,
        "pr_title": "Sample Feature",
        "pr_body": "Adding sample code",
        "pr_diff": "diff --git a/app.py b/app.py\n+print('hello')",
        "author_login": "alice",
        "head_commit_sha": "sha123",
        "base_branch": "main",
        "changed_files": ["app.py"],
        "retrieved_context": "",
        "confidence_threshold": 0.7,
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

    with patch("app.review.graph.save_review", new_callable=AsyncMock) as mock_save:
        final_state = await review_graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": "org/repo:1:sha123"}},
        )
        assert final_state["status"] == ReviewStatus.COMPLETED
        assert final_state["verdict"] in (ReviewVerdict.APPROVE, ReviewVerdict.REQUEST_CHANGES)
        assert len(final_state["agent_results"]) == 2
        mock_save.assert_awaited_once()
