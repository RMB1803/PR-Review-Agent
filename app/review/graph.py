import asyncio
import logging
import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.database.connection import SessionLocal
from app.database.repository import save_review
from app.github.client import GitHubClient
from app.memory.retriever import retrieve_context_for_diff
from app.review.agents import QualityAgent, SecurityAgent
from app.review.aggregator import (
    aggregate_agent_findings,
    format_review_markdown,
)
from app.review.state import (
    AgentResultState,
    PRReviewState,
    ReviewStatus,
    ReviewVerdict,
)

logger = logging.getLogger(__name__)


async def build_context_node(state: PRReviewState) -> dict[str, Any]:
    """Retrieve relevant repository context for the PR diff."""
    repo = state["repo_full_name"]
    pr_diff = state.get("pr_diff", "")
    cfg = get_settings()

    retrieved_context = await retrieve_context_for_diff(
        pr_diff,
        repo,
        cfg,
    )

    return {
        "retrieved_context": retrieved_context,
        "status": ReviewStatus.IN_PROGRESS,
    }


async def fan_out_agents_node(state: PRReviewState) -> dict[str, Any]:
    """Run security and code-quality agents concurrently."""
    diff = state.get("pr_diff", "")
    title = state.get("pr_title", "")
    body = state.get("pr_body", "")
    repo = state.get("repo_full_name", "")
    context = state.get("retrieved_context", "")

    security_agent = SecurityAgent()
    quality_agent = QualityAgent()

    async def run_agent(agent_type: str, runner) -> AgentResultState:
        started = time.time()

        try:
            findings, _, _ = await asyncio.wait_for(
                runner(),
                timeout=60,
            )

            return {
                "agent_type": agent_type,
                "success": True,
                "error_message": "",
                "duration_seconds": round(time.time() - started, 2),
                "findings": findings,
            }

        except Exception as exc:
            logger.exception("Agent %s failed", agent_type)

            return {
                "agent_type": agent_type,
                "success": False,
                "error_message": str(exc),
                "duration_seconds": round(time.time() - started, 2),
                "findings": [],
            }

    results = await asyncio.gather(
        run_agent(
            "security",
            lambda: security_agent.analyze(
                diff, title, body, repo, context
            ),
        ),
        run_agent(
            "quality",
            lambda: quality_agent.analyze(
                diff, title, body, repo, context
            ),
        ),
    )

    return {"agent_results": results}


async def aggregate_results_node(
    state: PRReviewState,
) -> dict[str, Any]:
    """Combine findings and determine the review verdict."""
    return aggregate_agent_findings(state)


async def post_review_node(
    state: PRReviewState,
) -> dict[str, Any]:
    """Print the review, post it to GitHub, and save it to Tiger Cloud."""
    cfg = get_settings()
    repo = state["repo_full_name"]
    pr_number = state["pr_number"]
    body = format_review_markdown(state)

    print(f"\nPR REVIEW: {repo} #{pr_number}\n")
    print(body)
    print()

    review_posted = False
    github_review_id = None

    # Post the review to GitHub.
    if cfg.github_token and state.get("head_commit_sha"):
        verdict = state.get("verdict")

        event = (
            "REQUEST_CHANGES"
            if verdict == ReviewVerdict.REQUEST_CHANGES
            else "APPROVE"
        )

        try:
            async with GitHubClient(cfg) as client:
                response = await client.post_pr_review(
                    repo_full_name=repo,
                    pr_number=pr_number,
                    payload={
                        "commit_id": state["head_commit_sha"],
                        "body": body,
                        "event": event,
                        "comments": [],
                    },
                )

            review_posted = True
            github_review_id = response.get("id")

            logger.info(
                "GitHub review posted: %s",
                github_review_id,
            )

        except Exception:
            logger.exception("Failed to post GitHub review")

    # Save the review and findings to Tiger Cloud.
    try:
        async with SessionLocal() as session:
            await save_review(
                session=session,
                review_id=state["workflow_id"],
                repo_full_name=repo,
                pr_number=pr_number,
                pr_title=state.get("pr_title", ""),
                head_commit_sha=state.get("head_commit_sha", ""),
                pr_diff=state.get("pr_diff", ""),
                verdict=(
                    state["verdict"].value
                    if state.get("verdict")
                    else None
                ),
                status=ReviewStatus.COMPLETED.value,
                findings=state.get("final_findings", []),
                github_review_id=github_review_id,
            )

        logger.info("Review saved to Tiger Cloud")

    except Exception:
        logger.exception("Failed to save review to Tiger Cloud")

    return {
        "review_posted": review_posted,
        "github_review_id": github_review_id,
        "status": ReviewStatus.COMPLETED,
    }


def build_review_graph():
    """Build the LangGraph review workflow."""
    workflow = StateGraph(PRReviewState)

    workflow.add_node("build_context", build_context_node)
    workflow.add_node("fan_out_agents", fan_out_agents_node)
    workflow.add_node("aggregate_results", aggregate_results_node)
    workflow.add_node("post_review", post_review_node)

    workflow.add_edge(START, "build_context")
    workflow.add_edge("build_context", "fan_out_agents")
    workflow.add_edge("fan_out_agents", "aggregate_results")
    workflow.add_edge("aggregate_results", "post_review")
    workflow.add_edge("post_review", END)

    return workflow.compile()


review_graph = build_review_graph()