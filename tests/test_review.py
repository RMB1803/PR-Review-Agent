"""
Tests for LangGraph review workflow and aggregation logic.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.review.aggregator import aggregate_agent_findings, sort_findings
from app.review.graph import review_graph
from app.review.state import PRReviewState, ReviewStatus, ReviewVerdict


def make_state(agent_results):
    """Create a minimal state for aggregation tests."""
    return {
        "workflow_id": "org/repo:1:sha123",
        "repo_full_name": "org/repo",
        "pr_number": 1,
        "pr_title": "Sample Feature",
        "pr_body": "",
        "pr_diff": "",
        "author_login": "alice",
        "head_commit_sha": "sha123",
        "base_branch": "main",
        "changed_files": [],
        "retrieved_context": "",
        "agent_results": agent_results,
        "verdict": None,
        "final_findings": [],
        "review_posted": False,
        "github_review_id": None,
        "status": ReviewStatus.IN_PROGRESS,
    }


def test_sort_findings():
    findings = [
        {"severity": "low", "summary": "minor styling"},
        {"severity": "critical", "summary": "sql injection"},
        {"severity": "medium", "summary": "too complex"},
        {"severity": "high", "summary": "missing auth"},
    ]

    sorted_findings = sort_findings(findings)

    assert [f["severity"] for f in sorted_findings] == [
        "critical",
        "high",
        "medium",
        "low",
    ]


def test_aggregate_security_failure():
    state = make_state([
        {
            "agent_type": "security",
            "success": False,
            "error_message": "timeout",
            "duration_seconds": 1.0,
            "findings": [],
        },
        {
            "agent_type": "quality",
            "success": True,
            "error_message": "",
            "duration_seconds": 1.0,
            "findings": [],
        },
    ])

    result = aggregate_agent_findings(state)

    # A failed security review must not approve the PR.
    assert result["verdict"] == ReviewVerdict.REQUEST_CHANGES
    assert result["final_findings"] == []


def test_aggregate_approve():
    state = make_state([
        {
            "agent_type": "security",
            "success": True,
            "error_message": "",
            "duration_seconds": 1.0,
            "findings": [],
        },
        {
            "agent_type": "quality",
            "success": True,
            "error_message": "",
            "duration_seconds": 1.0,
            "findings": [
                {
                    "severity": "medium",
                    "summary": "Consider simplifying this function",
                }
            ],
        },
    ])

    result = aggregate_agent_findings(state)

    assert result["verdict"] == ReviewVerdict.APPROVE
    assert len(result["final_findings"]) == 1


def test_aggregate_blocking_finding():
    state = make_state([
        {
            "agent_type": "security",
            "success": True,
            "error_message": "",
            "duration_seconds": 1.0,
            "findings": [
                {
                    "severity": "high",
                    "summary": "Missing authorization check",
                }
            ],
        },
        {
            "agent_type": "quality",
            "success": True,
            "error_message": "",
            "duration_seconds": 1.0,
            "findings": [],
        },
    ])

    result = aggregate_agent_findings(state)

    assert result["verdict"] == ReviewVerdict.REQUEST_CHANGES
    assert len(result["final_findings"]) == 1


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
        "agent_results": [],
        "verdict": None,
        "final_findings": [],
        "review_posted": False,
        "github_review_id": None,
        "status": ReviewStatus.PENDING,
    }

    security_findings = []
    quality_findings = [
        {
            "agent_type": "quality",
            "severity": "medium",
            "category": "maintainability",
            "summary": "Consider improving readability",
            "file_path": "app.py",
            "line_start": 1,
            "line_end": 1,
            "suggestion": "Use a descriptive function name.",
        }
    ]

    with (
        patch(
            "app.review.graph.retrieve_context_for_diff",
            new_callable=AsyncMock,
            return_value="",
        ),
        patch(
            "app.review.graph.SecurityAgent.analyze",
            new_callable=AsyncMock,
            return_value=(security_findings, 0.95, "approve"),
        ),
        patch(
            "app.review.graph.QualityAgent.analyze",
            new_callable=AsyncMock,
            return_value=(quality_findings, 0.90, "approve"),
        ),
        patch(
            "app.review.graph.GitHubClient"
        ) as mock_github_client,
        patch(
            "app.review.graph.SessionLocal"
        ) as mock_session_local,
        patch(
            "app.review.graph.save_review",
            new_callable=AsyncMock,
        ) as mock_save,
    ):
        # Ensure the graph does not make a real GitHub request.
        mock_github_client.return_value.__aenter__.return_value = (
            mock_github_client.return_value
        )
        mock_github_client.return_value.__aexit__.return_value = False

        # Avoid opening a real database session.
        mock_session_local.return_value.__aenter__.return_value = (
            AsyncMock()
        )
        mock_session_local.return_value.__aexit__.return_value = False

        final_state = await review_graph.ainvoke(
            initial_state,
            config={
                "configurable": {
                    "thread_id": "org/repo:1:sha123"
                }
            },
        )

    assert final_state["status"] == ReviewStatus.COMPLETED
    assert final_state["verdict"] == ReviewVerdict.APPROVE
    assert len(final_state["agent_results"]) == 2
    assert len(final_state["final_findings"]) == 1
    mock_save.assert_awaited_once()