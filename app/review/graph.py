# app/review/graph.py
"""
LangGraph review workflow:
build_context -> fan_out_agents -> aggregate_results -> post_review
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.database.connection import get_engine
from app.database.repository import save_review
from app.github.client import GitHubClient, PostReviewPayload, ReviewEvent
from app.memory.retriever import retrieve_context_for_diff
from app.review.agents import QualityAgent, SecurityAgent
from app.review.aggregator import aggregate_agent_findings, format_review_markdown
from app.review.state import AgentResultState, PRReviewState, ReviewStatus, ReviewVerdict

logger = logging.getLogger(__name__)


async def build_context_node(state: PRReviewState) -> dict[str, Any]:
    """Fetches diff and context from GitHub if not already provided."""
    repo = state["repo_full_name"]
    pr_number = state["pr_number"]
    cfg = get_settings()

    pr_diff = state.get("pr_diff", "")
    changed_files = state.get("changed_files", [])

    if not pr_diff and cfg.github_token:
        try:
            async with GitHubClient(cfg) as client:
                metadata = await client.get_pr_metadata(repo, pr_number)
                pr_diff = await client.get_pr_diff(repo, pr_number)
                files = await client.get_pr_files(repo, pr_number)
                changed_files = [f.filename for f in files]
                return {
                    "pr_diff": pr_diff,
                    "changed_files": changed_files,
                    "pr_title": metadata.title,
                    "pr_body": metadata.body,
                    "author_login": metadata.author_login,
                    "head_commit_sha": metadata.head_sha,
                    "base_branch": metadata.base_branch,
                    "status": ReviewStatus.IN_PROGRESS,
                }
        except Exception as e:
            logger.warning("build_context_node | GitHub fetch fallback | error=%s", e)

    # RAG memory context retrieval
    retrieved_context = await retrieve_context_for_diff(pr_diff, repo, cfg)

    return {
        "pr_diff": pr_diff,
        "changed_files": changed_files,
        "retrieved_context": retrieved_context,
        "status": ReviewStatus.IN_PROGRESS,
    }


async def fan_out_agents_node(state: PRReviewState) -> dict[str, Any]:
    """Runs Security and Quality agents concurrently."""
    diff = state.get("pr_diff", "")
    title = state.get("pr_title", "")
    body = state.get("pr_body", "")
    repo = state.get("repo_full_name", "")
    context = state.get("retrieved_context", "")

    sec_agent = SecurityAgent()
    qual_agent = QualityAgent()

    async def _run_agent(agent_type: str, runner) -> AgentResultState:
        t0 = time.time()
        try:
            findings, conf, verd = await asyncio.wait_for(runner(), timeout=60.0)
            return {
                "agent_type": agent_type,
                "success": True,
                "error_message": "",
                "duration_seconds": round(time.time() - t0, 2),
                "findings": findings,
                "confidence": conf,
                "per_verdict": verd,
            }
        except Exception as exc:
            logger.error("fan_out | agent=%s failed: %s", agent_type, exc)
            return {
                "agent_type": agent_type,
                "success": False,
                "error_message": str(exc),
                "duration_seconds": round(time.time() - t0, 2),
                "findings": [],
                "confidence": 0.0,
                "per_verdict": "approve",
            }

    results = await asyncio.gather(
        _run_agent("security", lambda: sec_agent.analyze(diff, title, body, repo, context)),
        _run_agent("quality", lambda: qual_agent.analyze(diff, title, body, repo, context)),
    )

    return {"agent_results": list(results)}


async def aggregate_results_node(state: PRReviewState) -> dict[str, Any]:
    """Arbitrates verdicts and findings."""
    return aggregate_agent_findings(state)


async def post_review_node(state: PRReviewState) -> dict[str, Any]:
    """Posts review to GitHub and outputs report to terminal and database."""
    cfg = get_settings()
    repo = state["repo_full_name"]
    pr_number = state["pr_number"]
    body = format_review_markdown(state)

    # 1. Print review report to terminal
    print("\n" + "=" * 60)
    print(f"PR REVIEW COMPLETE: {repo} #{pr_number}")
    print("=" * 60)
    print(body)
    print("=" * 60 + "\n")

    # 2. Post to GitHub if configured
    review_posted = False
    github_review_id = None
    if cfg.github_token and state.get("head_commit_sha"):
        verdict = state.get("verdict")
        event = (
            ReviewEvent.REQUEST_CHANGES
            if verdict == ReviewVerdict.REQUEST_CHANGES
            else (ReviewEvent.APPROVE if verdict == ReviewVerdict.APPROVE else ReviewEvent.COMMENT)
        )
        try:
            async with GitHubClient(cfg) as client:
                resp = await client.post_pr_review(
                    repo_full_name=repo,
                    pr_number=pr_number,
                    payload=PostReviewPayload(
                        commit_id=state["head_commit_sha"],
                        body=body,
                        event=event,
                        comments=[],
                    ),
                )
                review_posted = True
                github_review_id = resp.id
                logger.info("post_review_node | posted to GitHub review_id=%s", resp.id)
        except Exception as e:
            logger.warning("post_review_node | failed to post to GitHub: %s", e)

    # 3. Persist review into PostgreSQL
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        factory = async_sessionmaker(bind=get_engine(), class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await save_review(
                session=session,
                review_id=state["workflow_id"],
                repo_full_name=repo,
                pr_number=pr_number,
                pr_title=state.get("pr_title", ""),
                head_commit_sha=state.get("head_commit_sha", ""),
                pr_diff=state.get("pr_diff", ""),
                verdict=state["verdict"].value if state.get("verdict") else None,
                status=ReviewStatus.COMPLETED.value,
                overall_confidence=state.get("overall_confidence", 0.0),
                needs_human_review=state.get("needs_human_review", False),
                human_review_reason=state.get("human_review_reason", ""),
                findings=state.get("final_findings", []),
                github_review_id=github_review_id,
            )
            logger.info("post_review_node | review persisted to database")
    except Exception as db_err:
        logger.warning("post_review_node | failed to save review in database: %s", db_err)

    return {
        "review_posted": review_posted,
        "github_review_id": github_review_id,
        "status": ReviewStatus.COMPLETED,
    }


def build_review_graph():
    """Constructs the LangGraph StateGraph workflow."""
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
