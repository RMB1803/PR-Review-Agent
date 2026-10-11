# app/review/aggregator.py
"""
Aggregates findings from Security and Quality agents, applies arbitration rules,
and formats markdown summaries for terminal and GitHub.
"""

from __future__ import annotations

import logging
from typing import Any

from app.review.state import (
    AgentResultState,
    FindingSeverity,
    PRReviewState,
    ReviewVerdict,
)

logger = logging.getLogger(__name__)


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {
        FindingSeverity.CRITICAL.value: 0,
        FindingSeverity.HIGH.value: 1,
        FindingSeverity.MEDIUM.value: 2,
        FindingSeverity.LOW.value: 3,
        FindingSeverity.INFO.value: 4,
    }
    return sorted(findings, key=lambda f: order.get(f.get("severity", "low").lower(), 3))


def aggregate_agent_findings(state: PRReviewState) -> dict[str, Any]:
    """
    Arbitrates agent findings:
    - Combines all findings from successful agents.
    - If any agent finds CRITICAL or HIGH, requests changes.
    - If security agent failed, escalates to needs human review.
    - Otherwise approves.
    """
    results: list[AgentResultState] = state.get("agent_results", [])
    threshold = state.get("confidence_threshold", 0.7)

    # Security agent check
    security_result = next((r for r in results if r["agent_type"] == "security"), None)
    if security_result is None or not security_result["success"]:
        return {
            "verdict": ReviewVerdict.NEEDS_HUMAN_REVIEW,
            "final_findings": [],
            "overall_confidence": 0.0,
            "needs_human_review": True,
            "human_review_reason": "Security agent failed to run.",
        }

    all_findings: list[dict[str, Any]] = []
    total_conf = 0.0
    successful_agents = 0

    for r in results:
        if r["success"]:
            successful_agents += 1
            total_conf += r.get("confidence", 0.0)
            all_findings.extend(r.get("findings", []))

    overall_confidence = total_conf / successful_agents if successful_agents > 0 else 0.0
    sorted_findings = sort_findings(all_findings)

    # Escalation if confidence is lower than threshold
    if overall_confidence < threshold and sorted_findings:
        return {
            "verdict": ReviewVerdict.NEEDS_HUMAN_REVIEW,
            "final_findings": sorted_findings,
            "overall_confidence": overall_confidence,
            "needs_human_review": True,
            "human_review_reason": f"Overall confidence ({overall_confidence:.2f}) below threshold ({threshold:.2f}).",
        }

    has_blocking = any(
        f.get("severity", "").lower() in ("critical", "high")
        for f in sorted_findings
    )
    verdict = ReviewVerdict.REQUEST_CHANGES if has_blocking else ReviewVerdict.APPROVE

    return {
        "verdict": verdict,
        "final_findings": sorted_findings,
        "overall_confidence": overall_confidence,
        "needs_human_review": False,
        "human_review_reason": "",
    }


def format_review_markdown(state: PRReviewState) -> str:
    """Builds a rich markdown summary of the review."""
    verdict = state.get("verdict")
    findings = state.get("final_findings", [])
    conf = state.get("overall_confidence", 0.0)
    workflow_id = state.get("workflow_id", "")

    if verdict == ReviewVerdict.APPROVE:
        badge = "🟢 **APPROVED** — No critical or high severity issues identified."
    elif verdict == ReviewVerdict.REQUEST_CHANGES:
        badge = "🔴 **CHANGES REQUESTED** — Issues identified that must be resolved."
    else:
        badge = "⚠️ **NEEDS HUMAN REVIEW** — Confidence threshold not met or security review incomplete."

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev = f.get("severity", "low").lower()
        if sev in counts:
            counts[sev] += 1

    summary_parts = []
    if counts["critical"]:
        summary_parts.append(f"🔴 {counts['critical']} Critical")
    if counts["high"]:
        summary_parts.append(f"🟠 {counts['high']} High")
    if counts["medium"]:
        summary_parts.append(f"🟡 {counts['medium']} Medium")
    if counts["low"]:
        summary_parts.append(f"🔵 {counts['low']} Low")
    summary_line = " · ".join(summary_parts) if summary_parts else "No findings."

    # Findings list
    items = []
    for f in findings:
        sev = f.get("severity", "LOW").upper()
        agent = f.get("agent_type", "agent").capitalize()
        file_path = f.get("file_path", "general")
        line = f.get("line_start")
        line_str = f":{line}" if line else ""
        desc = f.get("summary", "")
        sug = f.get("suggestion", "")
        items.append(f"- **[{sev}]** `{file_path}{line_str}` — {desc} _({agent})_")
        if sug:
            items.append(f"  > 💡 {sug}")

    findings_text = "\n".join(items) if items else "_No issues found._"

    return f"""## 🤖 AI PR Review Report

{badge}

**Confidence:** {conf:.0%}  
**Summary:** {summary_line}

### Detailed Findings
{findings_text}

---
<sub>Workflow: `{workflow_id}`</sub>
"""
